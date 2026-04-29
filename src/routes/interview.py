"""
Interview Management Routes
API endpoints for managing technical interviews with LiveKit integration.
"""

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.connection import SessionLocal, get_db
from helpers.config import get_settings
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interviews", tags=["interviews"])


# ── Request / Response models ─────────────────────────────────────────────────

class InterviewStartRequest(BaseModel):
    application_id: int = Field(..., description="Application ID whose session to activate")


class InterviewStartResponse(BaseModel):
    session_id: int                 # TechnicalInterviewSession PK
    livekit_room_name: str
    livekit_token: str
    status: str
    message: str


class InterviewStatus(BaseModel):
    session_id: int
    status: str
    overall_score: float = 0.0      # never null — safe for React .toFixed()
    summary: Optional[str] = None


class CandidateScorecard(BaseModel):
    session_id: int
    application_id: int
    candidate_name: str
    overall_score: float
    overall_performance: Optional[str]
    recommendation: Optional[str]
    summary: Optional[str]
    transcript: Optional[str]
    status: str
    started_at: Optional[str]
    ended_at: Optional[str]


# ── POST /interviews/start ────────────────────────────────────────────────────

@router.post("/start", response_model=InterviewStartResponse)
async def start_interview(
    request: InterviewStartRequest,
    db: Session = Depends(get_db),
):
    """
    Activate the interview session for a candidate:

    1. Look up the existing TechnicalInterviewSession for this application.
    2. Set overall_score=0.0 immediately (prevents React toFixed(NULL) crash).
    3. Assign a room_name and mark status='Active'.
    4. Generate a signed LiveKit participant token.
    5. Create the LiveKit room with room metadata (used by the agent worker).
    6. Dispatch the AI interview agent to that room.
    7. Return the token so the frontend can join.
    """
    settings = get_settings()

    # ── 1. Validate application ───────────────────────────────────────────────
    application: Optional[Application] = (
        db.query(Application)
        .filter(Application.application_id == request.application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")

    candidate: Candidate = application.candidate
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")

    # ── 2. Find the existing TechnicalInterviewSession ────────────────────────
    session_row: Optional[TechnicalInterviewSession] = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.application_id == request.application_id)
        .first()
    )
    if not session_row:
        raise HTTPException(
            status_code=404,
            detail=(
                "No interview session found for this application. "
                "The interview invitation must be sent first."
            ),
        )

    if session_row.status == "Completed":
        raise HTTPException(
            status_code=400,
            detail="This interview has already been completed.",
        )

    # ── 3. Set preliminary score + activate session ───────────────────────────
    # Write 0.0 immediately so the React frontend never reads NULL while the
    # agent is conducting the interview (prevents .toFixed() crash).
    if session_row.overall_score is None:
        session_row.overall_score = 0.0

    room_name = session_row.room_name
    if not room_name:
        room_name = f"interview-{session_row.session_id}"
        session_row.room_name = room_name

    session_row.status = "Active"
    session_row.started_at = datetime.utcnow()
    db.commit()
    db.refresh(session_row)

    # ── 4. Resolve job title for agent metadata ───────────────────────────────
    posting: Optional[JobPosting] = application.posting if hasattr(application, "posting") else (
        db.query(JobPosting)
        .filter(JobPosting.posting_id == application.posting_id)
        .first()
    )
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == application.requisition_id)
        .first()
    ) if application.requisition_id else None

    job_title = (
        jr.job_title if jr and jr.job_title
        else posting.job_title if posting and hasattr(posting, "job_title")
        else "Technical Position"
    )
    candidate_name = f"{candidate.first_name or ''} {candidate.last_name or ''}".strip() or "Candidate"
    requisition_id = jr.requisition_id if jr else 0

    # ── 5. Build room metadata (read by the agent worker on connect) ──────────
    room_metadata = json.dumps({
        "session_id": session_row.session_id,
        "application_id": request.application_id,
        "candidate_name": candidate_name,
        "job_title": job_title,
        "requisition_id": requisition_id,
    })

    # ── 6. Generate signed LiveKit participant token ───────────────────────────
    livekit_token = _generate_livekit_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        room_name=room_name,
        participant_identity=f"candidate_{request.application_id}",
        participant_name=candidate_name,
    )

    # ── 7. Create LiveKit room + dispatch agent ────────────────────────────────
    try:
        await _create_room_and_dispatch_agent(
            livekit_url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            room_name=room_name,
            room_metadata=room_metadata,
            agent_name=settings.LIVEKIT_AGENT_NAME,
        )
    except Exception as exc:
        logger.error(
            "[interview/start] Failed to create room or dispatch agent: %s", exc
        )
        raise HTTPException(
            status_code=502,
            detail=f"LiveKit infrastructure error: {exc}",
        )

    return InterviewStartResponse(
        session_id=session_row.session_id,
        livekit_room_name=room_name,
        livekit_token=livekit_token,
        status="Active",
        message=(
            f"Interview room '{room_name}' is ready. "
            f"The AI interviewer will join shortly."
        ),
    )


# ── GET /interviews/status/{session_id} ───────────────────────────────────────

@router.get("/status/{session_id}", response_model=InterviewStatus)
def get_interview_status(
    session_id: int,
    db: Session = Depends(get_db),
):
    """
    Polling endpoint for the frontend. Safe to call while the interview is
    in progress — overall_score is initialised to 0.0 so it is never NULL.
    """
    row = _get_session_or_404(session_id, db)
    return InterviewStatus(
        session_id=row.session_id,
        status=row.status,
        overall_score=float(row.overall_score) if row.overall_score is not None else 0.0,
        summary=row.summary,
    )


# ── GET /interviews/scorecard/{session_id} ────────────────────────────────────

@router.get("/scorecard/{session_id}", response_model=CandidateScorecard)
def get_candidate_scorecard(
    session_id: int,
    db: Session = Depends(get_db),
):
    """
    Full interview result. Returns the transcript and combined score once
    status == 'Completed'.
    """
    row = _get_session_or_404(session_id, db)

    application = row.application
    candidate = application.candidate if application else None
    candidate_name = (
        f"{candidate.first_name or ''} {candidate.last_name or ''}".strip()
        if candidate else "Unknown"
    )

    return CandidateScorecard(
        session_id=row.session_id,
        application_id=row.application_id,
        candidate_name=candidate_name,
        overall_score=float(row.overall_score) if row.overall_score is not None else 0.0,
        overall_performance=row.overall_performance,
        recommendation=row.recommendation,
        summary=row.summary,
        transcript=row.transcript,
        status=row.status,
        started_at=row.started_at.isoformat() if row.started_at else None,
        ended_at=row.ended_at.isoformat() if row.ended_at else None,
    )


# ── POST /interviews/complete/{session_id} ────────────────────────────────────

@router.post("/complete/{session_id}")
def mark_interview_completed(
    session_id: int,
    db: Session = Depends(get_db),
):
    """
    Fallback endpoint — marks the session Completed without a score.
    Normally the agent worker writes the score via _persist_completion()
    in livekit_agent.py. This endpoint exists for webhook / manual override.
    """
    row = _get_session_or_404(session_id, db)

    if row.status == "Completed":
        return {"status": "already_completed", "session_id": session_id}

    row.status = "Completed"
    row.ended_at = datetime.utcnow()
    if row.overall_score is None:
        row.overall_score = 0.0
    db.commit()

    # Trigger final ranking check
    application = row.application
    posting = application.posting if application else None
    if posting and hasattr(posting, "requisition_id"):
        try:
            from tasks.interview_tasks import maybe_dispatch_final_ranking
            maybe_dispatch_final_ranking.delay(posting.requisition_id)
        except Exception as exc:
            logger.warning("[interview/complete] Final ranking dispatch failed: %s", exc)

    return {"status": "completed", "session_id": session_id}


# ── POST /hr-interviews/start ─────────────────────────────────────────────────

@router.post("/hr/start", response_model=InterviewStartResponse)
async def start_hr_interview(
    request: InterviewStartRequest,
    db: Session = Depends(get_db),
):
    """
    Activate the AI HR interview session for a candidate:

    1. Look up the existing HRInterviewSession for this application.
    2. Assign a room_name, mark status='Active'.
    3. Build room metadata with mode='hr' so the LiveKit agent uses HR prompts.
    4. Generate a signed LiveKit participant token.
    5. Create the LiveKit room and dispatch the AI agent.
    6. Return the token so the frontend can join.
    """
    settings = get_settings()

    application: Optional[Application] = (
        db.query(Application)
        .filter(Application.application_id == request.application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")

    candidate: Candidate = application.candidate
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")

    session_row: Optional[HRInterviewSession] = (
        db.query(HRInterviewSession)
        .filter(HRInterviewSession.application_id == request.application_id)
        .first()
    )
    if not session_row:
        raise HTTPException(
            status_code=404,
            detail="No HR interview session found. The HR invitation must be sent first.",
        )

    if session_row.status == "Completed":
        raise HTTPException(status_code=400, detail="This HR interview has already been completed.")

    if session_row.overall_score is None:
        session_row.overall_score = 0.0

    room_name = session_row.room_name
    if not room_name:
        room_name = f"hr-interview-{session_row.session_id}"
        session_row.room_name = room_name

    session_row.status = "Active"
    session_row.started_at = datetime.utcnow()
    db.commit()
    db.refresh(session_row)

    posting: Optional[JobPosting] = (
        db.query(JobPosting)
        .filter(JobPosting.posting_id == application.posting_id)
        .first()
    )
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == posting.requisition_id)
        .first()
    ) if posting else None

    job_title = jr.job_title if jr and jr.job_title else "the position"
    job_responsibilities = jr.key_responsibilities or "" if jr else ""
    candidate_name = f"{candidate.first_name or ''} {candidate.last_name or ''}".strip() or "Candidate"
    requisition_id = jr.requisition_id if jr else 0

    room_metadata = json.dumps({
        "session_id": session_row.session_id,
        "application_id": request.application_id,
        "candidate_name": candidate_name,
        "job_title": job_title,
        "requisition_id": requisition_id,
        "mode": "hr",
        "job_responsibilities": job_responsibilities,
    })

    livekit_token = _generate_livekit_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        room_name=room_name,
        participant_identity=f"candidate_{request.application_id}",
        participant_name=candidate_name,
    )

    try:
        await _create_room_and_dispatch_agent(
            livekit_url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            room_name=room_name,
            room_metadata=room_metadata,
            agent_name=settings.LIVEKIT_AGENT_NAME,
        )
    except Exception as exc:
        logger.error("[hr-interview/start] Failed to create room or dispatch agent: %s", exc)
        raise HTTPException(status_code=502, detail=f"LiveKit infrastructure error: {exc}")

    return InterviewStartResponse(
        session_id=session_row.session_id,
        livekit_room_name=room_name,
        livekit_token=livekit_token,
        status="Active",
        message=f"HR interview room '{room_name}' is ready. The AI HR interviewer will join shortly.",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: int, db: Session) -> TechnicalInterviewSession:
    row = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.session_id == session_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Interview session {session_id} not found.")
    return row


def _generate_livekit_token(
    api_key: str,
    api_secret: str,
    room_name: str,
    participant_identity: str,
    participant_name: str,
) -> str:
    """
    Generate a signed LiveKit JWT that allows the participant to join the room.
    Uses livekit-api (already in requirements).
    """
    from livekit.api import AccessToken, VideoGrants

    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_grants(VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    return token


async def _create_room_and_dispatch_agent(
    livekit_url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
    room_metadata: str,
    agent_name: str,
) -> None:
    """
    1. Creates the LiveKit room with metadata so the agent worker can read it.
    2. Dispatches the named agent worker to that room.

    Requires livekit-api >= 0.7 for AgentDispatch support.
    """
    from livekit.api import LiveKitAPI
    from livekit.api.models import CreateRoomRequest

    async with LiveKitAPI(
        url=livekit_url,
        api_key=api_key,
        api_secret=api_secret,
    ) as lk:
        # Create room with metadata (idempotent — LiveKit returns existing room if name matches)
        await lk.room.create_room(
            CreateRoomRequest(
                name=room_name,
                metadata=room_metadata,
            )
        )

        # Dispatch agent worker — requires the agent process to be running with
        #   python src/livekit_agent.py start
        # and the LIVEKIT_AGENT_NAME env var to match agent_name.
        try:
            from livekit.api.models import CreateAgentDispatchRequest
            await lk.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room_name,
                    metadata=room_metadata,
                )
            )
            logger.info(
                "[interview/start] Agent '%s' dispatched to room '%s'.",
                agent_name, room_name,
            )
        except AttributeError:
            # livekit-api < 0.7 does not have agent_dispatch; agent will auto-join
            # if the LiveKit server is configured with auto-dispatch for this room.
            logger.warning(
                "[interview/start] AgentDispatch not available (livekit-api < 0.7). "
                "Ensure the agent worker auto-dispatches or upgrade livekit-api."
            )

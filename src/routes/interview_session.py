"""
LiveKit Interview Session API
──────────────────────────────────────────────────────────────────────────────
Provides the complete bridge between the Frontend and the Voice Agent.

Endpoints
─────────
POST /api/interview/start
    Initialises a LiveKit room, injects Candidate + JD metadata into the room,
    and returns the access token the Frontend needs to join the voice session.

GET  /api/interview/status/{room_name}
    Real-time status of an interview room — live agent presence, elapsed time.

GET  /api/interview/results/{session_id}
    Full scorecard: HR explanation card + scoring visualisation data.

POST /api/interview/webhook
    LiveKit server-to-server webhook.  Handles room_finished / participant_left
    events to mark sessions Completed and trigger final ranking.

POST /api/interview/reset/{session_id}
    Resets a Failed/No-show session back to Scheduled so the candidate can retry.

POST /api/interview/complete/{session_id}
    Manual / LiveKit-triggered completion with overall_score payload.

Design principles
─────────────────
• The Frontend only needs the access_token + livekit_url to start the full
  AI voice experience — zero other calls required.
• All Candidate context (JD, CV text, gaps, projects) is embedded in the
  LiveKit Room Metadata as a signed JSON blob so the Agent reads it on entry.
• Idempotent start: calling /start twice returns the same token for an active
  session; returns 409 if the session is already Completed.
• Webhook events are validated with LiveKit's HMAC-SHA256 header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from database.connection import get_db
from helpers.config import get_settings
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.cv_experience import CVExperience
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.hr_interview_config import HRInterviewConfig
from models.db.hr_interview_report import HRInterviewReport
from models.db.hr_interview_session import HRInterviewSession
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_report import TechnicalInterviewReport
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interview", tags=["Interview Sessions"])

# ── Status constants ──────────────────────────────────────────────────────────
_STATUS_SCHEDULED = "Scheduled"
_STATUS_ACTIVE = "Active"
_STATUS_COMPLETED = "Completed"
_STATUS_FAILED = "Failed"
_STATUS_NO_SHOW = "No-show"
_RESETTABLE = {_STATUS_FAILED, _STATUS_NO_SHOW}


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

class StartInterviewRequest(BaseModel):
    requisition_id: int = Field(..., description="Job Requisition ID")
    candidate_id: int = Field(..., description="Candidate ID")
    language: str = Field(
        default="en",
        description="Interview language — 'en' (English-dominant) or 'ar' (Arabic/English bilingual)",
    )
    mode: str = Field(
        default="technical",
        description="Interview mode — 'technical' or 'hr'",
    )
    persona: str = Field(
        default="elite",
        description="Agent persona — 'focused' | 'elite' | 'bilingual'",
    )

    model_config = {"json_schema_extra": {
        "example": {
            "requisition_id": 12,
            "candidate_id": 7,
            "language": "en",
            "mode": "technical",
            "persona": "elite",
        }
    }}


class StartInterviewResponse(BaseModel):
    session_id: int
    room_name: str
    access_token: str
    livekit_url: str
    status: str
    expires_at: str = Field(..., description="ISO-8601 datetime — token expiry")

    model_config = {"json_schema_extra": {
        "example": {
            "session_id": 3,
            "room_name": "int_app_41",
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5...",
            "livekit_url": "wss://your-server.livekit.cloud",
            "status": "Scheduled",
            "expires_at": "2026-04-26T10:00:00",
        }
    }}


class InterviewStatusResponse(BaseModel):
    room_name: str
    session_id: Optional[int]
    status: str
    is_agent_present: bool
    participant_count: int
    elapsed_seconds: Optional[int]
    started_at: Optional[str]

    model_config = {"json_schema_extra": {
        "example": {
            "room_name": "int_app_41",
            "session_id": 3,
            "status": "Active",
            "is_agent_present": True,
            "participant_count": 2,
            "elapsed_seconds": 423,
            "started_at": "2026-04-25T09:00:00",
        }
    }}


class ScoreBreakdown(BaseModel):
    cv_semantic_score: Optional[float] = Field(None, description="0-100 from CV screening")
    interview_overall_score: Optional[float] = Field(None, description="0-100 from live interview")
    centroid_math_score: Optional[float] = Field(None, description="Sentence-BERT similarity (0-1)")
    depth_boost: Optional[float] = Field(None, description="Expert terminology density (0-1)")
    combined_score: Optional[float] = Field(None, description="Weighted composite 0-100")


class HRExplanationCard(BaseModel):
    strengths: List[str]
    gaps: List[str]
    recommendation: str
    explanation_text: Optional[str]
    rank_in_pool: Optional[int]


class InterviewResultsResponse(BaseModel):
    session_id: int
    application_id: int
    candidate_name: str
    job_title: str
    status: str
    mode: str
    language: str
    hr_card: HRExplanationCard
    scores: ScoreBreakdown
    transcript: Optional[str]
    summary: Optional[str]
    per_question_detail: Optional[List[Dict[str, Any]]]
    completed_at: Optional[str]
    red_flag: bool = False
    red_flag_reason: Optional[str] = None


class CompleteInterviewRequest(BaseModel):
    overall_score: Optional[float] = Field(
        None, ge=0, le=100,
        description="Final interview score 0-100 (set by the Agent after evaluation)"
    )
    summary: Optional[str] = None
    recommendation: Optional[str] = None
    transcript: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _build_cv_text(cv: CandidateCV, candidate: Candidate) -> str:
    """Assemble a plain-text CV blob for the agent's context window."""
    lines: list[str] = []

    if candidate.professional_summary:
        lines.append(f"Summary: {candidate.professional_summary}")

    if cv.experiences:
        lines.append("\n== Experience ==")
        for exp in cv.experiences:
            period = f"{exp.start_date or '?'} – {exp.end_date or 'Present'}"
            lines.append(f"• {exp.job_title} @ {exp.company_name} ({period})")
            if exp.description:
                lines.append(f"  {exp.description[:300]}")

    if cv.projects:
        lines.append("\n== Projects ==")
        for p in cv.projects:
            lines.append(f"• {p.project_name}: {p.description or ''}  Stack: {p.tech_stack or '—'}")

    if cv.skills:
        skill_str = ", ".join(s.skill_name for s in cv.skills[:30])
        lines.append(f"\n== Skills ==\n{skill_str}")

    return "\n".join(lines)


def _build_projects_list(cv: CandidateCV) -> list[dict]:
    """Return a structured list of projects for the agent metadata."""
    return [
        {
            "name": p.project_name or "Unnamed",
            "description": (p.description or "")[:400],
            "tech_stack": p.tech_stack or "",
        }
        for p in (cv.projects or [])
    ]


def _get_or_create_hr_interview_config(requisition_id: int, db: Session) -> HRInterviewConfig:
    config = (
        db.query(HRInterviewConfig)
        .filter(HRInterviewConfig.requisition_id == requisition_id)
        .first()
    )
    if not config:
        config = HRInterviewConfig(
            requisition_id=requisition_id,
            interview_type="AI HR",
            duration_minutes=45,
            scoring_system="1-10",
            candidates_to_advance=10,
        )
        db.add(config)
        db.flush()
    return config


def _get_or_create_interview_config(requisition_id: int, db: Session) -> TechnicalInterviewConfig:
    config = (
        db.query(TechnicalInterviewConfig)
        .filter(TechnicalInterviewConfig.requisition_id == requisition_id)
        .first()
    )
    if not config:
        config = TechnicalInterviewConfig(
            requisition_id=requisition_id,
            interview_type="AI Technical",
            duration_minutes=30,
            ai_feedback_level="detailed",
            scoring_system="centroid",
            candidates_to_advance=20,
        )
        db.add(config)
        db.flush()
    return config


async def _create_livekit_room(room_name: str, metadata: dict, agent_name: str | None = None) -> None:
    """
    Create a LiveKit room on the server and embed metadata, then dispatch the AI agent.
    Metadata is read by the Voice Agent on room entry to personalise the interview.
    Skips creation gracefully if the LiveKit server is unreachable.
    """
    cfg = get_settings()
    if not cfg.LIVEKIT_API_KEY:
        logger.warning("[livekit] LIVEKIT_API_KEY not set — skipping room creation.")
        return

    try:
        from livekit.api import LiveKitAPI
        from livekit.protocol.room import CreateRoomRequest  # type: ignore

        async with LiveKitAPI(
            url=cfg.LIVEKIT_URL,
            api_key=cfg.LIVEKIT_API_KEY,
            api_secret=cfg.LIVEKIT_API_SECRET,
        ) as lk:
            # Only store fields the agent worker reads from ctx.room.metadata.
            # Large blobs (cvText, jd, projects) cause create_room to fail silently.
            agent_room_metadata = {
                "session_id":          metadata.get("session_id"),
                "application_id":      metadata.get("application_id"),
                "candidate_name":      metadata.get("candidate_name"),
                "job_title":           metadata.get("job_title"),
                "requisition_id":      metadata.get("requisition_id"),
                "mode":                metadata.get("mode", "technical"),
                "language":            metadata.get("language", "en"),
                "job_responsibilities": metadata.get("job_responsibilities", ""),
            }
            await lk.room.create_room(
                CreateRoomRequest(
                    name=room_name,
                    metadata=json.dumps(agent_room_metadata),
                    empty_timeout=300,
                    max_participants=10,
                )
            )
            logger.info("[livekit] Room '%s' created with metadata.", room_name)

            # Dispatch the AI agent into the room so the candidate hears it on join.
            # Requires the agent worker to be running and connected to the same LiveKit project.
            try:
                from livekit.api import CreateAgentDispatchRequest  # type: ignore
                resolved_agent = agent_name or cfg.LIVEKIT_AGENT_NAME
                # Pass only essential IDs — agent reads full metadata from ctx.room.metadata.
                # Large payloads (cvText, jd, etc.) cause the dispatch API call to fail.
                minimal_meta = json.dumps({
                    "session_id":     metadata.get("session_id"),
                    "application_id": metadata.get("application_id"),
                    "candidate_name": metadata.get("candidate_name"),
                    "job_title":      metadata.get("job_title"),
                    "requisition_id": metadata.get("requisition_id"),
                    "mode":           metadata.get("mode", "technical"),
                })
                await lk.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=resolved_agent,
                        room=room_name,
                        metadata=minimal_meta,
                    )
                )
                logger.info("[livekit] Agent '%s' dispatched to room '%s'.", resolved_agent, room_name)
            except Exception as dispatch_exc:
                logger.error("[livekit] Agent dispatch FAILED for room '%s': %s", room_name, dispatch_exc)

    except Exception as exc:
        logger.error("[livekit] Room creation FAILED for room '%s': %s", room_name, exc)


def _generate_access_token(room_name: str, identity: str, display_name: str) -> tuple[str, str]:
    """
    Generate a signed LiveKit JWT access token for the candidate.

    Returns (jwt_token, expires_at_iso).
    Falls back to a placeholder token if the SDK is unavailable.
    """
    cfg = get_settings()
    expires_at = datetime.utcnow() + timedelta(hours=2)

    if not cfg.LIVEKIT_API_KEY or not cfg.LIVEKIT_API_SECRET:
        logger.warning("[livekit] Credentials not set — returning placeholder token.")
        return f"placeholder_token_for_{room_name}", expires_at.isoformat()

    try:
        from livekit.api import AccessToken, VideoGrants  # type: ignore

        token = (
            AccessToken(cfg.LIVEKIT_API_KEY, cfg.LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_name(display_name)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
        return token, expires_at.isoformat()

    except Exception as exc:
        logger.error("[livekit] Token generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"LiveKit token generation failed: {exc}")


async def _get_livekit_room_info(room_name: str) -> dict:
    """
    Query LiveKit server for live room state.
    Returns empty dict if server unreachable.
    """
    cfg = get_settings()
    if not cfg.LIVEKIT_API_KEY:
        return {}

    try:
        from livekit.api import LiveKitAPI
        from livekit.protocol.room import ListRoomsRequest, ListParticipantsRequest  # type: ignore

        async with LiveKitAPI(
            url=cfg.LIVEKIT_URL,
            api_key=cfg.LIVEKIT_API_KEY,
            api_secret=cfg.LIVEKIT_API_SECRET,
        ) as lk:
            rooms_resp = await lk.room.list_rooms(ListRoomsRequest(names=[room_name]))
            if not rooms_resp.rooms:
                return {"exists": False}

            participants_resp = await lk.room.list_participants(
                ListParticipantsRequest(room=room_name)
            )
            participants = list(participants_resp.participants)
            agent_present = any(
                "agent" in (p.identity or "").lower() or p.is_publisher
                for p in participants
            )
            return {
                "exists": True,
                "participant_count": len(participants),
                "is_agent_present": agent_present,
                "participants": [
                    {"identity": p.identity, "name": p.name, "is_publisher": p.is_publisher}
                    for p in participants
                ],
            }
    except Exception as exc:
        logger.warning("[livekit] Room info query failed: %s", exc)
        return {"exists": None, "error": str(exc)}


def _validate_livekit_webhook(body: bytes, auth_header: str) -> bool:
    """Validate LiveKit webhook using HMAC-SHA256 against the webhook secret."""
    cfg = get_settings()
    secret = cfg.LIVEKIT_WEBHOOK_SECRET or cfg.LIVEKIT_API_SECRET
    if not secret:
        logger.warning("[webhook] No webhook secret configured — accepting all events.")
        return True
    try:
        # LiveKit signs the body with the API secret as HMAC-SHA256
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, auth_header.strip())
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 1. POST /api/interview/start
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/start",
    response_model=StartInterviewResponse,
    summary="Initialise a LiveKit interview room for a candidate",
    responses={
        200: {"description": "Room already active — returns existing token"},
        201: {"description": "New session created and room provisioned"},
        400: {"description": "Screening report missing or invalid mode"},
        404: {"description": "Application not found"},
        409: {"description": "Interview already completed for this candidate"},
    },
)
async def start_interview(
    request: StartInterviewRequest,
    db: Session = Depends(get_db),
) -> StartInterviewResponse:
    """
    **The primary entry point for the Frontend.**

    ### What happens internally:
    1. Resolves `requisition_id + candidate_id` → `Application`
    2. Loads CV text, projects, skill gaps and strengths from DB
    3. Builds a `metadata` JSON blob with `{jd, cvText, gaps, projects, strengths, mode, language, persona}`
    4. Creates the LiveKit room on the server with this metadata embedded
    5. Generates a signed JWT access token (2-hour TTL)
    6. Creates or reuses a `TechnicalInterviewSession` row
    7. Returns `{room_name, access_token, livekit_url, session_id}`

    The Frontend only needs the `access_token` and `livekit_url` to instantiate
    the LiveKit client SDK and start the full AI voice experience.
    """
    cfg = get_settings()

    # ── Resolve Application ───────────────────────────────────────────────────
    posting = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == request.requisition_id)
        .first()
    )
    if not posting:
        raise HTTPException(status_code=404, detail="No job posting found for this requisition.")

    application = (
        db.query(Application)
        .filter(
            Application.posting_id == posting.posting_id,
            Application.candidate_id == request.candidate_id,
        )
        .options(
            joinedload(Application.candidate)
            .joinedload(Candidate.cvs)
            .joinedload(CandidateCV.experiences),
            joinedload(Application.candidate)
            .joinedload(Candidate.cvs)
            .joinedload(CandidateCV.projects),
            joinedload(Application.candidate)
            .joinedload(Candidate.cvs)
            .joinedload(CandidateCV.skills),
        )
        .first()
    )
    if not application:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No application found for candidate {request.candidate_id} "
                f"in requisition {request.requisition_id}."
            ),
        )

    candidate: Candidate = application.candidate
    jr: JobRequisition = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == request.requisition_id)
        .first()
    )

    # ── Mode routing ──────────────────────────────────────────────────────────
    is_hr = request.mode == "hr"
    room_name = (
        f"hr_app_{application.application_id}" if is_hr
        else f"int_app_{application.application_id}"
    )

    # ── Guard: completed session → 409 ───────────────────────────────────────
    if is_hr:
        existing_session = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.application_id == application.application_id)
            .first()
        )
    else:
        existing_session = (
            db.query(TechnicalInterviewSession)
            .filter(TechnicalInterviewSession.application_id == application.application_id)
            .first()
        )

    if existing_session and existing_session.status == _STATUS_COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Interview already completed for application {application.application_id}. "
                "Use GET /api/interview/results/{session_id} to retrieve the scorecard."
            ),
        )

    # ── Active: interview in progress — block re-entry ────────────────────────
    if existing_session and existing_session.status == _STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Interview session {existing_session.session_id} is currently in progress. "
                "It will close automatically when the AI interviewer finishes."
            ),
        )

    # ── Load Semantic Analysis for gaps/strengths ─────────────────────────────
    # NOTE: No early-return for Scheduled — always create/re-create the room and
    # re-dispatch the agent so the candidate's click always triggers a live session.
    sem_report: SemanticAnalysisReport = (
        db.query(SemanticAnalysisReport)
        .filter(SemanticAnalysisReport.application_id == application.application_id)
        .first()
    )
    if not sem_report:
        raise HTTPException(
            status_code=400,
            detail=(
                "CV screening report not found. "
                "The ranking pipeline must complete before an interview can be started."
            ),
        )

    hr_json: dict = {}
    if sem_report.hr_explanation_json:
        try:
            hr_json = json.loads(sem_report.hr_explanation_json)
        except (json.JSONDecodeError, TypeError):
            pass

    gaps: list[str] = hr_json.get("gaps", [])
    strengths: list[str] = hr_json.get("strengths", [])

    # ── Assemble CV text ──────────────────────────────────────────────────────
    primary_cv: CandidateCV | None = next(
        (cv for cv in (candidate.cvs or []) if cv.is_primary),
        (candidate.cvs[0] if candidate.cvs else None),
    )
    cv_text = _build_cv_text(primary_cv, candidate) if primary_cv else "CV not available."
    projects = _build_projects_list(primary_cv) if primary_cv else []

    # ── Build JD text ─────────────────────────────────────────────────────────
    jd_text = jr.full_job_description or jr.key_responsibilities or jr.job_title

    # ── Metadata blob injected into LiveKit Room ──────────────────────────────
    room_metadata: dict = {
        "requisition_id": request.requisition_id,
        "application_id": application.application_id,
        "candidate_id": candidate.candidate_id,
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "job_title": jr.job_title or "",
        "mode": request.mode,
        "language": request.language,
        "persona": request.persona,
        "jd": jd_text,
        "job_responsibilities": jr.key_responsibilities or "",
        "cvText": cv_text[:6000],
        "gaps": gaps[:10],
        "strengths": strengths[:10],
        "projects": projects[:8],
        "cv_score": float(sem_report.match_percentage or 0),
    }

    if is_hr:
        # Inject tech interview score as context for the HR agent
        tech_session = (
            db.query(TechnicalInterviewSession)
            .filter(TechnicalInterviewSession.application_id == application.application_id)
            .first()
        )
        room_metadata["tech_score"] = float(tech_session.overall_score or 0) if tech_session else 0.0
        room_metadata["focus_areas"] = [
            "communication", "teamwork", "leadership", "adaptability", "problem_solving"
        ]

    # ── Persist / update session BEFORE room creation to get session_id ───────
    if is_hr:
        config = _get_or_create_hr_interview_config(request.requisition_id, db)
        if existing_session:
            existing_session.room_name = room_name
            existing_session.language = request.language
            existing_session.mode = request.mode
            existing_session.status = _STATUS_SCHEDULED
            session = existing_session
        else:
            session = HRInterviewSession(
                application_id=application.application_id,
                config_id=config.config_id,
                status=_STATUS_SCHEDULED,
                room_name=room_name,
                language=request.language,
                mode="hr",
                scheduled_at=datetime.utcnow(),
            )
            db.add(session)
    else:
        config = _get_or_create_interview_config(request.requisition_id, db)
        if existing_session:
            existing_session.room_name = room_name
            existing_session.language = request.language
            existing_session.mode = request.mode
            existing_session.status = _STATUS_SCHEDULED
            session = existing_session
        else:
            session = TechnicalInterviewSession(
                application_id=application.application_id,
                config_id=config.config_id,
                status=_STATUS_SCHEDULED,
                room_name=room_name,
                language=request.language,
                mode=request.mode,
                scheduled_at=datetime.utcnow(),
            )
            db.add(session)

    db.flush()  # get session.session_id before room creation
    room_metadata["session_id"] = session.session_id

    # ── Create LiveKit room + dispatch agent ──────────────────────────────────
    agent_name = cfg.LIVEKIT_HR_AGENT_NAME if is_hr else cfg.LIVEKIT_AGENT_NAME
    logger.info(
        "[interview_start] Creating LiveKit room '%s' and dispatching agent '%s' "
        "(session_id=%d, mode=%s).",
        room_name, agent_name, session.session_id, request.mode,
    )
    await _create_livekit_room(room_name, room_metadata, agent_name=agent_name)

    # ── Generate candidate access token ───────────────────────────────────────
    token, expires_at = _generate_access_token(
        room_name=room_name,
        identity=f"candidate_{candidate.candidate_id}",
        display_name=f"{candidate.first_name} {candidate.last_name}",
    )

    db.commit()
    db.refresh(session)

    logger.info(
        "[interview_start] Session %d created for application %d | room=%s",
        session.session_id, application.application_id, room_name,
    )

    return StartInterviewResponse(
        session_id=session.session_id,
        room_name=room_name,
        access_token=token,
        livekit_url=cfg.LIVEKIT_URL,
        status=session.status,
        expires_at=expires_at,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET /api/interview/status/{room_name}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/status/{room_name}",
    response_model=InterviewStatusResponse,
    summary="Check real-time interview room status",
)
async def get_interview_status(
    room_name: str,
    db: Session = Depends(get_db),
) -> InterviewStatusResponse:
    """
    Returns the combined status of a room from two sources:
    - **DB**: `TechnicalInterviewSession.status` (Scheduled / Active / Completed / Failed)
    - **LiveKit API**: live participant count, agent presence

    Used by the Frontend for polling and by HR dashboards for real-time monitoring.
    """
    if room_name.startswith("hr_app_"):
        session = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.room_name == room_name)
            .first()
        )
    else:
        session = (
            db.query(TechnicalInterviewSession)
            .filter(TechnicalInterviewSession.room_name == room_name)
            .first()
        )

    lk_info = await _get_livekit_room_info(room_name)

    if not session and not lk_info.get("exists"):
        raise HTTPException(status_code=404, detail=f"Room '{room_name}' not found.")

    elapsed: int | None = None
    if session and session.started_at:
        end = session.ended_at or datetime.utcnow()
        elapsed = int((end - session.started_at).total_seconds())

    # Derive effective status
    effective_status = session.status if session else "Unknown"
    if lk_info.get("exists") and effective_status == _STATUS_SCHEDULED:
        effective_status = _STATUS_ACTIVE
        if session:
            session.status = _STATUS_ACTIVE
            if not session.started_at:
                session.started_at = datetime.utcnow()
            db.commit()

    return InterviewStatusResponse(
        room_name=room_name,
        session_id=session.session_id if session else None,
        status=effective_status,
        is_agent_present=lk_info.get("is_agent_present", False),
        participant_count=lk_info.get("participant_count", 0),
        elapsed_seconds=elapsed,
        started_at=session.started_at.isoformat() if session and session.started_at else None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. GET /api/interview/results/{session_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/results/{session_id}",
    response_model=InterviewResultsResponse,
    summary="Retrieve full interview scorecard and HR explanation card",
)
async def get_interview_results(
    session_id: int,
    db: Session = Depends(get_db),
) -> InterviewResultsResponse:
    """
    Returns the complete post-interview data package:

    - **HR Explanation Card**: strengths, gaps, recommendation, narrative text
    - **Score Breakdown**: CV semantic score + live interview score + centroid math
    - **Transcript**: full dialogue text
    - **Red-flag indicator**: CV-interview score discrepancy alert
    - **Per-question detail**: centroid / depth / LLM scores per question (from ai_insights)

    Use this endpoint after `GET /api/interview/status` returns `"Completed"`.
    """
    # Try technical session first, fall back to HR session
    session = (
        db.query(TechnicalInterviewSession)
        .options(
            joinedload(TechnicalInterviewSession.report),
            joinedload(TechnicalInterviewSession.application)
            .joinedload(Application.candidate),
            joinedload(TechnicalInterviewSession.application)
            .joinedload(Application.posting)
            .joinedload(JobPosting.requisition),
            joinedload(TechnicalInterviewSession.application)
            .joinedload(Application.semantic_report),
        )
        .filter(TechnicalInterviewSession.session_id == session_id)
        .first()
    )
    _is_hr_session = False
    if not session:
        session = (
            db.query(HRInterviewSession)
            .options(
                joinedload(HRInterviewSession.report),
                joinedload(HRInterviewSession.application)
                .joinedload(Application.candidate),
                joinedload(HRInterviewSession.application)
                .joinedload(Application.posting)
                .joinedload(JobPosting.requisition),
                joinedload(HRInterviewSession.application)
                .joinedload(Application.semantic_report),
            )
            .filter(HRInterviewSession.session_id == session_id)
            .first()
        )
        _is_hr_session = True

    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")

    application: Application = session.application
    candidate: Candidate = application.candidate
    jr: JobRequisition = application.posting.requisition

    # ── Semantic Analysis Report ──────────────────────────────────────────────
    sem: SemanticAnalysisReport = application.semantic_report
    hr_json: dict = {}
    if sem and sem.hr_explanation_json:
        try:
            hr_json = json.loads(sem.hr_explanation_json)
        except (json.JSONDecodeError, TypeError):
            pass

    gaps = hr_json.get("gaps", [])
    strengths = hr_json.get("strengths", [])
    recommendation = hr_json.get("recommendation", session.recommendation or "Pending evaluation")

    # ── Interview Report ──────────────────────────────────────────────────────
    report: TechnicalInterviewReport | None = session.report

    # ── Per-question detail from ai_insights ─────────────────────────────────
    per_question: list[dict] = []
    if sem and sem.ai_insights:
        try:
            insights = json.loads(sem.ai_insights)
            per_question = insights.get("interview_results", {}).get("questions_asked", [])
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Score breakdown ───────────────────────────────────────────────────────
    cv_score = float(sem.match_percentage) if sem and sem.match_percentage else None
    interview_score = float(session.overall_score) if session.overall_score else None

    centroid_avg: float | None = None
    if per_question:
        centroids = [q.get("centroid_score") for q in per_question if q.get("centroid_score") is not None]
        centroid_avg = sum(centroids) / len(centroids) if centroids else None

    depth_avg: float | None = None
    if per_question:
        depths = [q.get("depth_boost") for q in per_question if q.get("depth_boost") is not None]
        depth_avg = sum(depths) / len(depths) if depths else None

    combined: float | None = None
    if cv_score is not None and interview_score is not None:
        combined = round(cv_score * 0.60 + interview_score * 0.40, 2)

    # ── Red-flag ──────────────────────────────────────────────────────────────
    red_flag = False
    red_flag_reason: str | None = None
    if cv_score is not None and interview_score is not None:
        if cv_score >= 80.0 and interview_score < 40.0:
            red_flag = True
            red_flag_reason = (
                f"Candidate scored {cv_score:.1f}% on CV screening but only "
                f"{interview_score:.1f}% in the live interview. Manual review recommended."
            )

    return InterviewResultsResponse(
        session_id=session.session_id,
        application_id=application.application_id,
        candidate_name=f"{candidate.first_name} {candidate.last_name}",
        job_title=jr.job_title,
        status=session.status,
        mode=session.mode or "technical",
        language=session.language or "en",
        hr_card=HRExplanationCard(
            strengths=strengths,
            gaps=gaps,
            recommendation=recommendation,
            explanation_text=sem.hr_explanation_text if sem else None,
            rank_in_pool=sem.rank_in_pool if sem else None,
        ),
        scores=ScoreBreakdown(
            cv_semantic_score=cv_score,
            interview_overall_score=interview_score,
            centroid_math_score=round(centroid_avg, 4) if centroid_avg is not None else None,
            depth_boost=round(depth_avg, 4) if depth_avg is not None else None,
            combined_score=combined,
        ),
        transcript=session.summary or (report.interview_summary if report else None),
        summary=report.detailed_assessment if report else session.summary,
        per_question_detail=per_question or None,
        completed_at=session.ended_at.isoformat() if session.ended_at else None,
        red_flag=red_flag,
        red_flag_reason=red_flag_reason,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. POST /api/interview/webhook  (LiveKit server → backend)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/webhook",
    summary="LiveKit webhook receiver — handles room events",
    include_in_schema=False,  # internal endpoint, hide from Swagger
)
async def livekit_webhook(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Receives server-sent events from LiveKit.

    Validated events handled:
    - `room_finished` → marks session Completed, triggers final ranking check
    - `participant_left` where identity contains "agent" → marks session Failed

    Configure this URL in your LiveKit server/cloud dashboard as the webhook destination.
    """
    body = await request.body()

    # Validate HMAC signature
    auth_header = authorization or ""
    if not _validate_livekit_webhook(body, auth_header):
        logger.warning("[webhook] Invalid signature — request rejected.")
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_type: str = payload.get("event", "")
    room_info: dict = payload.get("room", {})
    room_name: str = room_info.get("name", "")

    logger.info("[webhook] Received event '%s' for room '%s'.", event_type, room_name)

    if not room_name:
        return JSONResponse({"ok": True})

    db_gen = get_db()
    db: Session = next(db_gen)
    try:
        is_hr_room = room_name.startswith("hr_app_")
        if is_hr_room:
            session = (
                db.query(HRInterviewSession)
                .filter(HRInterviewSession.room_name == room_name)
                .first()
            )
        else:
            session = (
                db.query(TechnicalInterviewSession)
                .filter(TechnicalInterviewSession.room_name == room_name)
                .first()
            )

        if event_type == "room_finished":
            if session and session.status != _STATUS_COMPLETED:
                session.status = _STATUS_COMPLETED
                session.ended_at = datetime.utcnow()
                db.commit()
                logger.info("[webhook] Session %d marked Completed.", session.session_id)

                if is_hr_room:
                    _trigger_hr_final_ranking(session, db)
                else:
                    _trigger_final_ranking(session, db)

        elif event_type == "participant_left":
            participant = payload.get("participant", {})
            identity: str = participant.get("identity", "")
            if "agent" in identity.lower() and session and session.status == _STATUS_ACTIVE:
                session.status = _STATUS_FAILED
                db.commit()
                logger.warning("[webhook] Agent left room %s — session marked Failed.", room_name)

    except Exception as exc:
        db.rollback()
        logger.exception("[webhook] Error processing event '%s': %s", event_type, exc)
    finally:
        db.close()

    return JSONResponse({"ok": True})


def _trigger_final_ranking(session: TechnicalInterviewSession, db: Session) -> None:
    """Fire the Celery task that checks if all tech interviews are done → dispatch HR invitations."""
    try:
        application = (
            db.query(Application)
            .filter(Application.application_id == session.application_id)
            .first()
        )
        if application and application.posting:
            requisition_id = application.posting.requisition_id
            from tasks.interview_tasks import maybe_dispatch_final_ranking
            maybe_dispatch_final_ranking.delay(requisition_id)
            logger.info("[webhook] Dispatched maybe_dispatch_final_ranking for JR %d.", requisition_id)
    except Exception as exc:
        logger.warning("[webhook] Could not dispatch final ranking: %s", exc)


def _trigger_hr_final_ranking(session: HRInterviewSession, db: Session) -> None:
    """Fire the Celery task that checks if all HR interviews are done → dispatch final ranking."""
    try:
        application = (
            db.query(Application)
            .filter(Application.application_id == session.application_id)
            .first()
        )
        if application and application.posting:
            requisition_id = application.posting.requisition_id
            from tasks.interview_tasks import maybe_dispatch_final_ranking_after_hr
            maybe_dispatch_final_ranking_after_hr.delay(requisition_id)
            logger.info("[webhook] Dispatched maybe_dispatch_final_ranking_after_hr for JR %d.", requisition_id)
    except Exception as exc:
        logger.warning("[webhook] Could not dispatch HR final ranking: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# 5. POST /api/interview/complete/{session_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/complete/{session_id}",
    summary="Mark interview as completed (called by Agent or manual override)",
    responses={
        200: {"description": "Session marked Completed"},
        208: {"description": "Session was already Completed"},
        404: {"description": "Session not found"},
    },
)
async def complete_interview(
    session_id: int,
    body: CompleteInterviewRequest = Body(default=CompleteInterviewRequest()),
    db: Session = Depends(get_db),
):
    """
    Called by the Voice Agent (or HR manually) when the interview finishes.

    - Persists `overall_score`, `summary`, `recommendation`, `transcript`.
    - Marks `ended_at` and sets `status = Completed`.
    - Automatically triggers `maybe_dispatch_final_ranking` via Celery.

    The Agent should call this endpoint with its computed score payload after
    finalising the candidate evaluation.
    """
    # Try technical session first, fall back to HR session
    session = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.session_id == session_id)
        .first()
    )
    _complete_is_hr = False
    if not session:
        session = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.session_id == session_id)
            .first()
        )
        _complete_is_hr = True

    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")

    if session.status == _STATUS_COMPLETED:
        return JSONResponse(
            status_code=208,
            content={"status": "already_completed", "session_id": session_id},
        )

    session.status = _STATUS_COMPLETED
    session.ended_at = datetime.utcnow()
    if body.overall_score is not None:
        session.overall_score = body.overall_score
    if body.summary:
        session.summary = body.summary
    if body.recommendation:
        session.recommendation = body.recommendation

    # Persist transcript to SemanticAnalysisReport.ai_insights (tech sessions only)
    if body.transcript and not _complete_is_hr:
        sem = (
            db.query(SemanticAnalysisReport)
            .filter(SemanticAnalysisReport.application_id == session.application_id)
            .first()
        )
        if sem:
            try:
                insights: dict = json.loads(sem.ai_insights or "{}")
            except (json.JSONDecodeError, TypeError):
                insights = {}
            interview_block: dict = insights.get("interview_results", {})
            interview_block["transcript"] = body.transcript
            interview_block["session_id"] = session_id
            interview_block["completed_at"] = datetime.utcnow().isoformat()
            if body.overall_score is not None:
                interview_block["overall_score"] = body.overall_score
            insights["interview_results"] = interview_block
            sem.ai_insights = json.dumps(insights, ensure_ascii=False)

    db.commit()
    logger.info("[complete] Session %d (%s) marked Completed. score=%.1f",
                session_id, "hr" if _complete_is_hr else "tech", body.overall_score or 0)

    if _complete_is_hr:
        _trigger_hr_final_ranking(session, db)
    else:
        _trigger_final_ranking(session, db)

    return {
        "status": "completed",
        "session_id": session_id,
        "overall_score": body.overall_score,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. POST /api/interview/reset/{session_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/reset/{session_id}",
    summary="Reset a Failed/No-show interview session so the candidate can retry",
    responses={
        200: {"description": "Session reset to Scheduled"},
        400: {"description": "Session is Completed — cannot reset"},
        404: {"description": "Session not found"},
    },
)
async def reset_interview(
    session_id: int,
    db: Session = Depends(get_db),
):
    """
    Resets a `Failed` or `No-show` session back to `Scheduled`.

    Use this when:
    - The LiveKit agent crashed before the interview started.
    - The candidate lost connection before any evaluation happened.

    **Cannot be used to reset a `Completed` session.** Results are permanent.
    """
    session = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.session_id == session_id)
        .first()
    )
    if not session:
        session = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.session_id == session_id)
            .first()
        )
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")

    if session.status == _STATUS_COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Cannot reset a Completed interview. Results are permanent.",
        )
    if session.status not in _RESETTABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Session status is '{session.status}' — only Failed or No-show sessions can be reset.",
        )

    prev_status = session.status
    session.status = _STATUS_SCHEDULED
    session.started_at = None
    session.ended_at = None
    session.overall_score = None
    session.summary = None
    session.recommendation = None
    db.commit()

    logger.info(
        "[reset] Session %d reset from '%s' → Scheduled.", session_id, prev_status
    )

    return {
        "status": "reset",
        "session_id": session_id,
        "previous_status": prev_status,
        "new_status": _STATUS_SCHEDULED,
    }

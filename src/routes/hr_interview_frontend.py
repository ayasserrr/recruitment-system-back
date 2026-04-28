"""
HR Interview dashboard endpoints.

GET  /api/v1/jobs/{jobId}/hr-interview                   — overview
GET  /api/v1/jobs/{jobId}/hr-interview/candidates        — candidate list
POST /api/v1/jobs/{jobId}/hr-interview/schedule          — schedule session
POST /api/v1/jobs/{jobId}/hr-interview/submit-scores     — submit scores
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.hr_interview_config import HRInterviewConfig
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.schemas.frontend_schemas import (
    HRInterviewCandidate,
    HRInterviewOverview,
    ScheduleInterviewRequest,
    SubmitHRScoresRequest,
    TranscriptRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["hr-interview"])


def _require_jr_posting(job_id: int, company_id: int, db: Session):
    jr = (
        db.query(JobRequisition)
        .filter(
            JobRequisition.requisition_id == job_id,
            JobRequisition.company_id == company_id,
        )
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    posting = (
        db.query(JobPosting).filter(JobPosting.requisition_id == job_id).first()
    )
    return jr, posting


def _parse_sub_scores(session: HRInterviewSession) -> dict:
    if not session.summary:
        return {}
    try:
        data = json.loads(session.summary)
        if isinstance(data, dict) and "sub_scores" in data:
            return data["sub_scores"]
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


# ── GET /api/v1/jobs/{jobId}/hr-interview ─────────────────────────────────────

@router.get(
    "/{job_id}/hr-interview",
    response_model=HRInterviewOverview,
    summary="HR interview overview",
)
def get_hr_interview_overview(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    config: Optional[HRInterviewConfig] = (
        db.query(HRInterviewConfig)
        .filter(HRInterviewConfig.requisition_id == job_id)
        .first()
    )

    sessions = []
    if posting:
        sessions = (
            db.query(HRInterviewSession)
            .join(Application, Application.application_id == HRInterviewSession.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )

    scheduled = len(sessions)
    completed = sum(1 for s in sessions if s.status == "Completed")
    pending = scheduled - completed

    scores = [float(s.overall_score) for s in sessions if s.overall_score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    upcoming = [
        s for s in sessions
        if s.status not in ("Completed", "No-show", "Cancelled")
        and s.scheduled_at is not None
    ]
    upcoming.sort(key=lambda s: s.scheduled_at)
    next_interview: Optional[str] = None
    if upcoming:
        next_interview = upcoming[0].scheduled_at.strftime("%Y-%m-%d %H:%M")

    primary_interviewer = sessions[0].interviewer_name if sessions else None

    duration_str = "N/A"
    if config and config.duration_minutes:
        duration_str = f"{config.duration_minutes} minutes"

    hr_status = "pending"
    if jr.status == "ranking_complete":
        hr_status = "completed"
    elif sessions:
        hr_status = "active"

    return HRInterviewOverview(
        id=config.config_id if config else 0,
        jobTitle=jr.job_title,
        scheduled=scheduled,
        completed=completed,
        pending=pending,
        avgScore=avg_score,
        nextInterview=next_interview,
        interviewer=primary_interviewer,
        duration=duration_str,
        passingScore=7.0,
        status=hr_status,
    )


# ── GET /api/v1/jobs/{jobId}/hr-interview/candidates ─────────────────────────

@router.get(
    "/{job_id}/hr-interview/candidates",
    response_model=List[HRInterviewCandidate],
    summary="HR interview candidate results",
)
def get_hr_interview_candidates(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        return []

    rows = (
        db.query(HRInterviewSession, Application, Candidate)
        .join(Application, Application.application_id == HRInterviewSession.application_id)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .filter(Application.posting_id == posting.posting_id)
        .order_by(HRInterviewSession.scheduled_at.asc())
        .all()
    )

    result: List[HRInterviewCandidate] = []
    for session, app, cand in rows:
        sub = _parse_sub_scores(session)
        overall = float(session.overall_score) if session.overall_score else None
        interview_date = session.scheduled_at.strftime("%Y-%m-%d") if session.scheduled_at else None

        feedback = session.recommendation
        if not feedback and session.summary:
            try:
                data = json.loads(session.summary)
                feedback = data.get("feedback")
            except (json.JSONDecodeError, TypeError):
                pass

        # Full candidate profile
        education_str: Optional[str] = None
        if cand.education_level:
            education_str = cand.education_level
            if cand.field_of_study:
                education_str += f" in {cand.field_of_study}"

        exp_label: Optional[str] = None
        if cand.years_of_experience:
            exp_label = f"{cand.years_of_experience}+ years"

        skills: List[str] = [
            s.skill_name
            for s in db.query(CVSkill).filter(CVSkill.cv_id == app.cv_id).limit(10).all()
        ] if app.cv_id else []

        projects: List[str] = [
            p.project_name
            for p in db.query(CVProject).filter(CVProject.cv_id == app.cv_id).limit(5).all()
            if p.project_name
        ] if app.cv_id else []

        result.append(
            HRInterviewCandidate(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                cultureFit=sub.get("cultureFit"),
                communication=sub.get("communication"),
                leadership=sub.get("leadership"),
                motivation=sub.get("motivation"),
                teamwork=sub.get("teamwork"),
                overall=overall,
                emotionScore=float(session.emotion_score) if session.emotion_score is not None else None,
                sentimentScore=float(session.sentiment_score) if session.sentiment_score is not None else None,
                nliAlignScore=float(session.nli_align_score) if session.nli_align_score is not None else None,
                semanticDepthScore=float(session.semantic_depth_score) if session.semantic_depth_score is not None else None,
                shapSummary=session.shap_summary,
                hasTranscript=bool(session.transcript),
                status=session.status or "Scheduled",
                interviewer=session.interviewer_name,
                date=interview_date,
                feedback=feedback,
                email=cand.email,
                phone=cand.phone,
                experience=exp_label,
                education=education_str,
                summary=cand.professional_summary,
                projects=projects,
                skills=skills,
            )
        )

    return result


# ── POST /api/v1/jobs/{jobId}/hr-interview/schedule ───────────────────────────

@router.post(
    "/{job_id}/hr-interview/schedule",
    status_code=status.HTTP_201_CREATED,
    summary="Schedule an HR interview session",
)
def schedule_hr_interview(
    job_id: int,
    body: ScheduleInterviewRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job posting not found.")

    config: Optional[HRInterviewConfig] = (
        db.query(HRInterviewConfig)
        .filter(HRInterviewConfig.requisition_id == job_id)
        .first()
    )
    if not config:
        config = HRInterviewConfig(
            requisition_id=job_id,
            interview_type=body.type,
            duration_minutes=30,
            scoring_system="1-10",
            candidates_to_advance=3,
        )
        db.add(config)
        db.flush()

    app: Optional[Application] = (
        db.query(Application)
        .filter(
            Application.posting_id == posting.posting_id,
            Application.candidate_id == body.candidateId,
        )
        .first()
    )
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate application not found for this job.",
        )

    existing: Optional[HRInterviewSession] = (
        db.query(HRInterviewSession)
        .filter(HRInterviewSession.application_id == app.application_id)
        .first()
    )
    scheduled_dt = datetime.strptime(
        f"{body.scheduledDate} {body.scheduledTime}", "%Y-%m-%d %H:%M"
    )

    if existing:
        existing.scheduled_at = scheduled_dt
        existing.interviewer_name = body.interviewerName
        db.commit()
        return {"message": "HR interview rescheduled.", "sessionId": existing.session_id}

    session = HRInterviewSession(
        application_id=app.application_id,
        config_id=config.config_id,
        status="Scheduled",
        scheduled_at=scheduled_dt,
        interviewer_name=body.interviewerName,
    )
    db.add(session)
    db.commit()

    return {"message": "HR interview scheduled.", "sessionId": session.session_id}


# ── POST /api/v1/jobs/{jobId}/hr-interview/submit-scores ─────────────────────

@router.post(
    "/{job_id}/hr-interview/submit-scores",
    summary="Submit HR interview scores",
)
def submit_hr_scores(
    job_id: int,
    body: SubmitHRScoresRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job posting not found.")

    app: Optional[Application] = (
        db.query(Application)
        .filter(
            Application.posting_id == posting.posting_id,
            Application.candidate_id == body.candidateId,
        )
        .first()
    )
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate application not found.",
        )

    session: Optional[HRInterviewSession] = (
        db.query(HRInterviewSession)
        .filter(HRInterviewSession.application_id == app.application_id)
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No HR interview session found for this candidate.",
        )

    sub_scores = {
        "cultureFit": body.cultureFit,
        "communication": body.communication,
        "leadership": body.leadership,
        "motivation": body.motivation,
        "teamwork": body.teamwork,
    }
    overall = round(sum(sub_scores.values()) / len(sub_scores), 2)

    session.overall_score = overall
    session.status = "Completed"
    session.ended_at = datetime.utcnow()
    session.recommendation = body.feedback
    session.summary = json.dumps({
        "sub_scores": sub_scores,
        "feedback": body.feedback,
    })
    if body.transcript:
        session.transcript = body.transcript

    db.commit()

    # Trigger final ranking if all HR sessions are now done
    try:
        from tasks.interview_tasks import maybe_dispatch_final_ranking_after_hr
        maybe_dispatch_final_ranking_after_hr.delay(jr.requisition_id)
    except Exception:
        logger.warning("[hr_submit] Failed to dispatch ranking check for JR %d.", jr.requisition_id)

    return {
        "message": "HR scores submitted.",
        "candidateId": body.candidateId,
        "overall": overall,
    }


# ── PATCH /api/v1/jobs/{jobId}/hr-interview/{candidateId}/transcript ──────────

@router.patch(
    "/{job_id}/hr-interview/{candidate_id}/transcript",
    summary="Store or update the HR interview transcript for AI analysis",
)
def update_hr_transcript(
    job_id: int,
    candidate_id: int,
    body: TranscriptRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job posting not found.")

    app: Optional[Application] = (
        db.query(Application)
        .filter(
            Application.posting_id == posting.posting_id,
            Application.candidate_id == candidate_id,
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate application not found.")

    session: Optional[HRInterviewSession] = (
        db.query(HRInterviewSession)
        .filter(HRInterviewSession.application_id == app.application_id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No HR interview session found for this candidate.")

    session.transcript = body.transcript
    db.commit()

    return {"message": "Transcript saved.", "candidateId": candidate_id}

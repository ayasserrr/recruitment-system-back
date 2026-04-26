"""
Technical Interview dashboard endpoints.

GET  /api/v1/jobs/{jobId}/technical-interview                   — overview
GET  /api/v1/jobs/{jobId}/technical-interview/candidates        — candidate list
POST /api/v1/jobs/{jobId}/technical-interview/schedule          — schedule session
POST /api/v1/jobs/{jobId}/technical-interview/submit-scores     — submit scores (human)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_session import TechnicalInterviewSession
from models.schemas.frontend_schemas import (
    ScheduleInterviewRequest,
    SubmitTechScoresRequest,
    TechInterviewCandidate,
    TechInterviewOverview,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["technical-interview"])


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
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == job_id)
        .first()
    )
    return jr, posting


def _parse_sub_scores(session: TechnicalInterviewSession) -> dict:
    """Parse per-criterion scores stored as JSON in session.summary."""
    if not session.summary:
        return {}
    try:
        data = json.loads(session.summary)
        if isinstance(data, dict) and "sub_scores" in data:
            return data["sub_scores"]
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


# ── GET /api/v1/jobs/{jobId}/technical-interview ──────────────────────────────

@router.get(
    "/{job_id}/technical-interview",
    response_model=TechInterviewOverview,
    summary="Technical interview overview",
)
def get_tech_interview_overview(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    config: Optional[TechnicalInterviewConfig] = (
        db.query(TechnicalInterviewConfig)
        .filter(TechnicalInterviewConfig.requisition_id == job_id)
        .first()
    )

    sessions = []
    if posting:
        sessions = (
            db.query(TechnicalInterviewSession)
            .join(Application, Application.application_id == TechnicalInterviewSession.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )

    scheduled = len(sessions)
    completed = sum(1 for s in sessions if s.status == "Completed")
    pending = scheduled - completed

    scores = [float(s.overall_score) for s in sessions if s.overall_score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    # Next upcoming interview
    upcoming = [
        s for s in sessions
        if s.status not in ("Completed", "No-show", "Cancelled")
        and s.scheduled_at is not None
    ]
    upcoming.sort(key=lambda s: s.scheduled_at)
    next_interview: Optional[str] = None
    if upcoming:
        next_interview = upcoming[0].scheduled_at.strftime("%Y-%m-%d %H:%M")

    interviewers = list({s.interviewer_name for s in sessions if s.interviewer_name})

    duration_str = "N/A"
    if config and config.duration_minutes:
        duration_str = f"{config.duration_minutes} minutes"

    inter_status = "pending"
    if jr.status == "ranking_complete":
        inter_status = "completed"
    elif jr.status in ("interview_pending", "assessment_ranked"):
        inter_status = "active"

    return TechInterviewOverview(
        id=config.config_id if config else 0,
        jobTitle=jr.job_title,
        scheduled=scheduled,
        completed=completed,
        pending=pending,
        avgScore=avg_score,
        nextInterview=next_interview,
        interviewers=interviewers,
        duration=duration_str,
        passingScore=7.0,
        status=inter_status,
    )


# ── GET /api/v1/jobs/{jobId}/technical-interview/candidates ───────────────────

@router.get(
    "/{job_id}/technical-interview/candidates",
    response_model=List[TechInterviewCandidate],
    summary="Technical interview candidate results",
)
def get_tech_interview_candidates(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        return []

    rows = (
        db.query(TechnicalInterviewSession, Application, Candidate)
        .join(Application, Application.application_id == TechnicalInterviewSession.application_id)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .filter(Application.posting_id == posting.posting_id)
        .order_by(TechnicalInterviewSession.scheduled_at.asc())
        .all()
    )

    result: List[TechInterviewCandidate] = []
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
            TechInterviewCandidate(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                technicalScore=sub.get("technicalScore"),
                problemSolving=sub.get("problemSolving"),
                systemDesign=sub.get("systemDesign"),
                coding=sub.get("coding"),
                communication=sub.get("communication"),
                overall=overall,
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


# ── POST /api/v1/jobs/{jobId}/technical-interview/schedule ────────────────────

@router.post(
    "/{job_id}/technical-interview/schedule",
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a technical interview session",
)
def schedule_tech_interview(
    job_id: int,
    body: ScheduleInterviewRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job posting not found.")

    config: Optional[TechnicalInterviewConfig] = (
        db.query(TechnicalInterviewConfig)
        .filter(TechnicalInterviewConfig.requisition_id == job_id)
        .first()
    )
    if not config:
        config = TechnicalInterviewConfig(
            requisition_id=job_id,
            interview_type=body.type,
            duration_minutes=45,
            scoring_system="1-10",
            candidates_to_advance=5,
        )
        db.add(config)
        db.flush()

    # Find application for the given candidateId
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

    existing: Optional[TechnicalInterviewSession] = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.application_id == app.application_id)
        .first()
    )
    if existing:
        # Update schedule
        scheduled_dt = datetime.strptime(
            f"{body.scheduledDate} {body.scheduledTime}", "%Y-%m-%d %H:%M"
        )
        existing.scheduled_at = scheduled_dt
        existing.interviewer_name = body.interviewerName
        db.commit()
        return {"message": "Interview rescheduled.", "sessionId": existing.session_id}

    scheduled_dt = datetime.strptime(
        f"{body.scheduledDate} {body.scheduledTime}", "%Y-%m-%d %H:%M"
    )
    session = TechnicalInterviewSession(
        application_id=app.application_id,
        config_id=config.config_id,
        status="Scheduled",
        scheduled_at=scheduled_dt,
        interviewer_name=body.interviewerName,
        mode=body.type,
    )
    db.add(session)
    db.commit()

    return {"message": "Interview scheduled.", "sessionId": session.session_id}


# ── POST /api/v1/jobs/{jobId}/technical-interview/submit-scores ───────────────

@router.post(
    "/{job_id}/technical-interview/submit-scores",
    summary="Submit technical interview scores (human-conducted)",
)
def submit_tech_scores(
    job_id: int,
    body: SubmitTechScoresRequest,
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

    session: Optional[TechnicalInterviewSession] = (
        db.query(TechnicalInterviewSession)
        .filter(TechnicalInterviewSession.application_id == app.application_id)
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interview session found for this candidate.",
        )

    sub_scores = {
        "technicalScore": body.technicalScore,
        "problemSolving": body.problemSolving,
        "systemDesign": body.systemDesign,
        "coding": body.coding,
        "communication": body.communication,
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

    db.commit()

    return {
        "message": "Scores submitted.",
        "candidateId": body.candidateId,
        "overall": overall,
    }

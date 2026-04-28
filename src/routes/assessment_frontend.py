"""
Technical Assessment dashboard endpoints.

GET  /api/v1/jobs/{jobId}/assessment                  — overview stats
GET  /api/v1/jobs/{jobId}/assessment/candidates        — ranked candidate results
POST /api/v1/jobs/{jobId}/assessment/send-invitations  — send links to candidates
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.assessment_report import AssessmentReport
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from models.schemas.frontend_schemas import (
    AssessmentCandidate,
    AssessmentOverview,
    SendInvitationsRequest,
    SendInvitationsResponse,
    match_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["assessment"])


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


def _score_to_technical_label(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Very Good"
    if score >= 55:
        return "Good"
    return "Average"


# ── GET /api/v1/jobs/{jobId}/assessment ───────────────────────────────────────

@router.get(
    "/{job_id}/assessment",
    response_model=AssessmentOverview,
    summary="Technical assessment overview",
)
def get_assessment_overview(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    config: Optional[TechnicalAssessmentConfig] = (
        db.query(TechnicalAssessmentConfig)
        .filter(TechnicalAssessmentConfig.requisition_id == job_id)
        .first()
    )
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment configured for this job.",
        )

    posting_id = posting.posting_id if posting else None

    total_candidates: int = (
        db.query(func.count(CandidateAssessment.assessment_id))
        .filter(CandidateAssessment.config_id == config.config_id)
        .scalar()
    ) or 0

    completed: int = (
        db.query(func.count(CandidateAssessment.assessment_id))
        .filter(
            CandidateAssessment.config_id == config.config_id,
            CandidateAssessment.status == "Submitted",
        )
        .scalar()
    ) or 0

    pending = total_candidates - completed

    # Average score from leaderboard (most accurate after ranking)
    avg_score_row = (
        db.query(func.avg(AssessmentLeaderboard.final_score))
        .filter(AssessmentLeaderboard.jr_id == job_id)
        .scalar()
    )
    avg_score = round(float(avg_score_row or 0), 2)

    # Number of questions
    from models.db.assessment_question_set import AssessmentQuestionSet
    question_count: int = (
        db.query(func.count(AssessmentQuestionSet.id))
        .filter(AssessmentQuestionSet.jr_id == job_id)
        .scalar()
    ) or 0

    # Assess status
    from datetime import datetime
    is_closed = (
        config.assessment_deadline is not None
        and config.assessment_deadline <= datetime.utcnow()
    )
    if jr.status in ("assessment_ranked", "interview_pending", "ranking_complete"):
        assess_status = "completed"
    elif jr.status == "assessment_sent":
        assess_status = "active"
    else:
        assess_status = "pending"

    duration_str = f"{config.time_limit_minutes} minutes" if config.time_limit_minutes else "N/A"

    return AssessmentOverview(
        id=config.config_id,
        jobTitle=jr.job_title,
        totalCandidates=total_candidates,
        sent=total_candidates,
        completed=completed,
        pending=pending,
        deadline=config.assessment_deadline.strftime("%Y-%m-%d") if config.assessment_deadline else None,
        status=assess_status,
        avgScore=avg_score,
        duration=duration_str,
        questions=question_count,
        passingScore=70.0,
    )


# ── GET /api/v1/jobs/{jobId}/assessment/candidates ────────────────────────────

@router.get(
    "/{job_id}/assessment/candidates",
    response_model=List[AssessmentCandidate],
    summary="Ranked assessment candidates",
)
def get_assessment_candidates(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    # Use leaderboard as primary source (most complete data)
    rows = (
        db.query(AssessmentLeaderboard, CandidateAssessment, Application, Candidate, AssessmentReport)
        .join(
            CandidateAssessment,
            CandidateAssessment.assessment_id == AssessmentLeaderboard.assessment_id,
        )
        .join(Application, Application.application_id == AssessmentLeaderboard.application_id)
        .join(Candidate, Candidate.candidate_id == AssessmentLeaderboard.candidate_id)
        .outerjoin(AssessmentReport, AssessmentReport.assessment_id == AssessmentLeaderboard.assessment_id)
        .filter(AssessmentLeaderboard.jr_id == job_id)
        .order_by(AssessmentLeaderboard.rank.asc())
        .all()
    )

    result: List[AssessmentCandidate] = []
    for lb, ca, app, cand, ar in rows:
        score = float(lb.final_score or 0)
        time_spent_str = "N/A"
        if ca.started_at and ca.submitted_at:
            diff_min = int((ca.submitted_at - ca.started_at).total_seconds() / 60)
            time_spent_str = f"{diff_min} min"

        completed_date = ca.submitted_at.strftime("%Y-%m-%d") if ca.submitted_at else None

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
            for s in db.query(CVSkill)
            .filter(CVSkill.cv_id == app.cv_id)
            .limit(10)
            .all()
        ] if app.cv_id else []

        projects: List[str] = [
            p.project_name
            for p in db.query(CVProject)
            .filter(CVProject.cv_id == app.cv_id)
            .limit(5)
            .all()
            if p.project_name
        ] if app.cv_id else []

        result.append(
            AssessmentCandidate(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                score=round(score, 1),
                technical=_score_to_technical_label(score),
                problemSolving=_score_to_technical_label(score),
                timeSpent=time_spent_str,
                status="passed" if (ca.passed is True) else (
                    "failed" if ca.passed is False else "pending"
                ),
                codingScore=None,
                theoryScore=None,
                completed=completed_date,
                shapSummary=ar.shap_summary if ar else None,
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


# ── POST /api/v1/jobs/{jobId}/assessment/send-invitations ─────────────────────

@router.post(
    "/{job_id}/assessment/send-invitations",
    response_model=SendInvitationsResponse,
    summary="Send assessment invitations to selected candidates",
)
def send_assessment_invitations(
    job_id: int,
    body: SendInvitationsRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    config: Optional[TechnicalAssessmentConfig] = (
        db.query(TechnicalAssessmentConfig)
        .filter(TechnicalAssessmentConfig.requisition_id == job_id)
        .first()
    )
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment configured for this job.",
        )

    sent, failed = 0, 0
    try:
        from helpers.config import get_settings
        from services.email_service import send_assessment_invitation_sync
        cfg = get_settings()
        base_url = cfg.APP_BASE_URL.rstrip("/")
        use_email = True
    except Exception:
        use_email = False

    for candidate_id in body.candidateIds:
        # candidateIds in this endpoint are candidate_id values
        app: Optional[Application] = (
            db.query(Application)
            .filter(
                Application.posting_id == posting.posting_id if posting else -1,
                Application.candidate_id == candidate_id,
            )
            .first()
        ) if posting else None

        if not app:
            failed += 1
            continue

        # Skip if already has an assessment
        existing: Optional[CandidateAssessment] = (
            db.query(CandidateAssessment)
            .filter(CandidateAssessment.application_id == app.application_id)
            .first()
        )
        if existing:
            sent += 1
            continue

        assessment = CandidateAssessment(
            application_id=app.application_id,
            config_id=config.config_id,
            status="Pending",
        )
        db.add(assessment)
        db.flush()

        if use_email:
            try:
                cand: Optional[Candidate] = (
                    db.query(Candidate)
                    .filter(Candidate.candidate_id == candidate_id)
                    .first()
                )
                if cand and cand.email:
                    assessment_url = f"{base_url}/assessment/{assessment.assessment_id}"
                    send_assessment_invitation_sync(
                        recipient_email=cand.email,
                        first_name=cand.first_name or "Candidate",
                        job_title=jr.job_title,
                        assessment_url=assessment_url,
                    )
                sent += 1
            except Exception:
                logger.warning("[send-invitations] Email failed for candidate %d.", candidate_id)
                sent += 1
        else:
            sent += 1

    db.commit()
    return SendInvitationsResponse(
        sent=sent,
        failed=failed,
        message=f"{sent} invitation(s) sent successfully.",
    )

"""
Dashboard-level Jobs API (authenticated).

GET  /api/v1/jobs              — list all jobs for the company with pipeline counts
GET  /api/v1/jobs/{id}         — single job dashboard detail with counts
PATCH /api/v1/jobs/{id}        — update job status
DELETE /api/v1/jobs/{id}       — delete job post and requisition
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate_assessment import CandidateAssessment
from models.db.final_ranking import FinalRanking
from models.db.hr_interview_config import HRInterviewConfig
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.posting_platform import PostingPlatform
from models.db.requisition_required_skill import RequisitionRequiredSkill
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_session import TechnicalInterviewSession
from models.schemas.frontend_schemas import (
    JobListItem,
    JobListResponse,
    JobStatusUpdate,
    jr_status_to_display,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["dashboard-jobs"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_jr(job_id: int, company_id: int, db: Session) -> JobRequisition:
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
    return jr


def _build_job_item(jr: JobRequisition, db: Session) -> JobListItem:
    posting: Optional[JobPosting] = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == jr.requisition_id)
        .first()
    )
    posting_id = posting.posting_id if posting else None

    posted_date: Optional[str] = None
    if posting and posting.posted_date:
        posted_date = posting.posted_date.strftime("%Y-%m-%d")
    elif jr.posting_start_date:
        posted_date = jr.posting_start_date.isoformat()

    # ── Auto-close display flag (read-only — never writes to DB) ──────────────
    end_date_passed = bool(
        jr.cv_collection_end_date and jr.cv_collection_end_date < date.today()
    )

    # ── Pipeline counts ────────────────────────────────────────────────────────
    cvs = 0
    new_today = 0
    semantic_count = 0
    assessment_count = 0
    tech_count = 0
    hr_count = 0
    final_count = 0

    if posting_id:
        today_start = datetime.combine(date.today(), datetime.min.time())

        cvs = (
            db.query(func.count(Application.application_id))
            .filter(Application.posting_id == posting_id)
            .scalar()
        ) or 0

        new_today = (
            db.query(func.count(Application.application_id))
            .filter(
                Application.posting_id == posting_id,
                Application.applied_at >= today_start,
            )
            .scalar()
        ) or 0

        semantic_count = (
            db.query(func.count(SemanticAnalysisReport.report_id))
            .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        ) or 0

        assessment_count = (
            db.query(func.count(CandidateAssessment.assessment_id))
            .join(Application, Application.application_id == CandidateAssessment.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        ) or 0

        tech_count = (
            db.query(func.count(TechnicalInterviewSession.session_id))
            .join(Application, Application.application_id == TechnicalInterviewSession.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        ) or 0

        hr_count = (
            db.query(func.count(HRInterviewSession.session_id))
            .join(Application, Application.application_id == HRInterviewSession.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        ) or 0

        final_count = (
            db.query(func.count(FinalRanking.ranking_id))
            .filter(
                FinalRanking.posting_id == posting_id,
                FinalRanking.final_status == "Selected",
            )
            .scalar()
        ) or 0

    # ── Platforms ──────────────────────────────────────────────────────────────
    platforms: List[str] = [
        p.platform_name
        for p in db.query(PostingPlatform)
        .filter(PostingPlatform.requisition_id == jr.requisition_id)
        .all()
    ]

    # ── Skills ─────────────────────────────────────────────────────────────────
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    for sk in db.query(RequisitionRequiredSkill).filter(
        RequisitionRequiredSkill.requisition_id == jr.requisition_id
    ).all():
        if sk.skill_type == "preferred":
            preferred_skills.append(sk.skill_name)
        else:
            required_skills.append(sk.skill_name)

    # ── Pipeline configs ───────────────────────────────────────────────────────
    assess_cfg: Optional[TechnicalAssessmentConfig] = (
        db.query(TechnicalAssessmentConfig)
        .filter(TechnicalAssessmentConfig.requisition_id == jr.requisition_id)
        .first()
    )
    tech_cfg: Optional[TechnicalInterviewConfig] = (
        db.query(TechnicalInterviewConfig)
        .filter(TechnicalInterviewConfig.requisition_id == jr.requisition_id)
        .first()
    )
    hr_cfg: Optional[HRInterviewConfig] = (
        db.query(HRInterviewConfig)
        .filter(HRInterviewConfig.requisition_id == jr.requisition_id)
        .first()
    )

    return JobListItem(
        id=jr.requisition_id,
        jobTitle=jr.job_title,
        department=jr.department,
        posted=posted_date,
        status=jr_status_to_display(jr.status or "Draft", end_date_passed),
        cvs=cvs,
        newToday=new_today,
        semantic=semantic_count,
        assessment=assessment_count,
        techInterview=tech_count,
        hrInterview=hr_count,
        finalCandidates=final_count,
        selectedPlatforms=platforms,
        postingStartDate=jr.posting_start_date.isoformat() if jr.posting_start_date else None,
        postingEndDate=jr.cv_collection_end_date.isoformat() if jr.cv_collection_end_date else None,
        requiredSkills=required_skills,
        preferredSkills=preferred_skills,
        assessmentCandidatesToAdvance=assess_cfg.candidates_to_advance if assess_cfg else None,
        technicalInterviewCandidatesToAdvance=tech_cfg.candidates_to_advance if tech_cfg else None,
        hrInterviewCandidatesToAdvance=hr_cfg.candidates_to_advance if hr_cfg else None,
        technicalInterviewDuration=(
            f"{tech_cfg.duration_minutes} minutes"
            if tech_cfg and tech_cfg.duration_minutes else None
        ),
        hrInterviewDuration=(
            f"{hr_cfg.duration_minutes} minutes"
            if hr_cfg and hr_cfg.duration_minutes else None
        ),
    )


# ── GET /api/v1/jobs ───────────────────────────────────────────────────────────

@router.get("", response_model=JobListResponse, summary="List all jobs for the company")
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    company_id = ctx["company_id"]
    base_q = (
        db.query(JobRequisition)
        .filter(JobRequisition.company_id == company_id)
        .order_by(JobRequisition.created_at.desc())
    )
    total = base_q.count()
    jrs = base_q.offset((page - 1) * page_size).limit(page_size).all()

    results = [_build_job_item(jr, db) for jr in jrs]
    return JobListResponse(count=total, results=results)


# ── GET /api/v1/jobs/{id} ──────────────────────────────────────────────────────

@router.get("/{job_id}", response_model=JobListItem, summary="Single job dashboard detail")
def get_job_detail(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr = _require_jr(job_id, ctx["company_id"], db)
    return _build_job_item(jr, db)


# ── PATCH /api/v1/jobs/{id} ────────────────────────────────────────────────────

_STATUS_CANONICAL: dict[str, str] = {
    # Normalise common frontend capitalization variants to the exact strings
    # expected by the Celery Beat scanners and state-machine guards.
    "active":           "Active",
    "published":        "published",
    "draft":            "Draft",
    "closed":           "Closed",
    "ranked":           "ranked",
    "assessment_sent":  "assessment_sent",
    "assessment_ranked": "assessment_ranked",
    "interview_pending": "interview_pending",
    "ranking_complete": "ranking_complete",
    # Common frontend typos / alternate forms
    "open":             "Active",
    "live":             "Active",
    "publish":          "published",
    "close":            "Closed",
}


@router.patch("/{job_id}", summary="Update job status")
def update_job_status(
    job_id: int,
    body: JobStatusUpdate,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr = _require_jr(job_id, ctx["company_id"], db)
    # Normalise status so "Published", "ACTIVE", "active" all resolve to the
    # canonical form checked by the Celery Beat scanners.
    canonical = _STATUS_CANONICAL.get(body.status.lower().strip(), body.status.strip())
    jr.status = canonical
    jr.updated_at = datetime.utcnow()
    db.commit()
    logger.info("[dashboard] JR %d status → '%s' (requested: '%s')", job_id, canonical, body.status)
    return {"id": job_id, "status": jr.status, "message": "Status updated."}


# ── DELETE /api/v1/jobs/{id} ───────────────────────────────────────────────────

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a job post")
def delete_job(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr = _require_jr(job_id, ctx["company_id"], db)
    db.delete(jr)
    db.commit()

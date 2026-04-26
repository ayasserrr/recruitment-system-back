"""
GET /api/v1/jobs/{jobId}/pipeline

Returns the 5-stage job-posting lifecycle for one job.
Stages: Job Post Created → Bias Detection → Posted to Platforms → Receiving CVs → Closed
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.posting_platform import PostingPlatform
from models.schemas.frontend_schemas import PipelineResponse, PipelineStage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["pipeline"])


def _fmt_date(dt) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    return str(dt)


def _fmt_time(dt) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "strftime"):
        return dt.strftime("%H:%M")
    return None


# ── GET /api/v1/jobs/{jobId}/pipeline ─────────────────────────────────────────

@router.get("/{job_id}/pipeline", response_model=PipelineResponse, summary="Job pipeline status")
def get_pipeline(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(
            JobRequisition.requisition_id == job_id,
            JobRequisition.company_id == ctx["company_id"],
        )
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    posting: Optional[JobPosting] = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == job_id)
        .first()
    )

    platforms: List[str] = [
        p.platform_name
        for p in db.query(PostingPlatform)
        .filter(PostingPlatform.requisition_id == job_id)
        .all()
    ]

    # ── Auto-close display flag (read-only — never writes to DB) ──────────────
    end_date_passed = bool(
        jr.cv_collection_end_date and jr.cv_collection_end_date < date.today()
    )

    # ── CV counts ──────────────────────────────────────────────────────────────
    cv_count = 0
    new_today = 0
    if posting:
        today_start = datetime.combine(date.today(), datetime.min.time())
        cv_count = (
            db.query(func.count(Application.application_id))
            .filter(Application.posting_id == posting.posting_id)
            .scalar()
        ) or 0
        new_today = (
            db.query(func.count(Application.application_id))
            .filter(
                Application.posting_id == posting.posting_id,
                Application.applied_at >= today_start,
            )
            .scalar()
        ) or 0

    jr_status = jr.status or "Draft"
    is_closed = end_date_passed or jr_status in ("Closed", "ranking_complete")
    is_published = jr_status not in ("Draft",) and posting is not None
    has_posting = posting is not None

    # ── Stage status derivation ────────────────────────────────────────────────
    if is_closed:
        created_status = "completed"
        bias_status = "completed"
        posted_status = "completed"
        receiving_status = "completed"
        closed_status = "completed"
    elif is_published:
        created_status = "completed"
        bias_status = "completed"
        posted_status = "completed"
        receiving_status = "active"
        closed_status = "pending"
    elif has_posting:
        # Posting exists but not yet fully active
        created_status = "completed"
        bias_status = "completed"
        posted_status = "active"
        receiving_status = "pending"
        closed_status = "pending"
    else:
        # Draft: JR created, bias detection in progress
        created_status = "completed"
        bias_status = "active"
        posted_status = "pending"
        receiving_status = "pending"
        closed_status = "pending"

    stages: List[PipelineStage] = [
        PipelineStage(
            step="Job Post Created",
            status=created_status,
            date=_fmt_date(jr.created_at),
            time=_fmt_time(jr.created_at),
        ),
        PipelineStage(
            step="Bias Detection",
            status=bias_status,
            note="AI scanned job description for biased language" if bias_status == "completed" else None,
        ),
        PipelineStage(
            step="Posted to Platforms",
            status=posted_status,
            date=_fmt_date(posting.posted_date if posting else jr.posting_start_date),
            platforms=platforms or None,
        ),
        PipelineStage(
            step="Receiving CVs",
            status=receiving_status,
            date=_fmt_date(jr.cv_collection_end_date),
            count=cv_count if receiving_status in ("active", "completed") else None,
            newToday=new_today if receiving_status == "active" else None,
        ),
        PipelineStage(
            step="Closed",
            status=closed_status,
            date=_fmt_date(jr.cv_collection_end_date) if is_closed else None,
            note=f"Closed with {cv_count} CVs received" if is_closed and cv_count else None,
        ),
    ]

    posting_end_date = jr.cv_collection_end_date.isoformat() if jr.cv_collection_end_date else None

    return PipelineResponse(
        jobId=job_id,
        jobTitle=jr.job_title,
        postingEndDate=posting_end_date,
        stages=stages,
    )

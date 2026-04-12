from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from models.db.job_requisition import JobRequisition

jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


# ── Response schema ────────────────────────────────────────────────────────

class JobPublicResponse(BaseModel):
    requisition_id: int
    job_title: str
    department: Optional[str]
    seniority_level: Optional[str]
    employment_type: Optional[str]
    location_city: Optional[str]
    location_country: Optional[str]
    remote_available: bool
    min_years_experience: Optional[int]
    max_years_experience: Optional[int]
    min_education_level: Optional[str]
    full_job_description: Optional[str]
    contact_email: Optional[str]
    cv_collection_end_date: Optional[str]   # ISO 8601
    is_open: bool

    class Config:
        from_attributes = True


# ── Shared deadline utility ────────────────────────────────────────────────

def check_job_deadline(requisition_id: int, db: Session) -> JobRequisition:
    """
    Fetch the requisition and raise 403 if cv_collection_end_date has passed.
    Raises 404 if the job does not exist.
    """
    req = db.query(JobRequisition).filter(
        JobRequisition.requisition_id == requisition_id
    ).first()

    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    if req.cv_collection_end_date is not None:
        today_utc = datetime.now(timezone.utc).date()
        if today_utc >= req.cv_collection_end_date:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="APPLICATION_CLOSED",
            )

    return req


def _is_open(req: JobRequisition) -> bool:
    """Return True only when there is a deadline and it is still in the future."""
    if req.cv_collection_end_date is None:
        return True   # no deadline set → treat as open
    return datetime.now(timezone.utc).date() < req.cv_collection_end_date


# ── GET /api/v1/jobs/{jid} ─────────────────────────────────────────────────

@jobs_router.get(
    "/{jid}",
    response_model=JobPublicResponse,
    summary="Public job details (no auth required)",
)
def get_job(jid: int, db: Session = Depends(get_db)):
    req = db.query(JobRequisition).filter(
        JobRequisition.requisition_id == jid
    ).first()

    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    return JobPublicResponse(
        requisition_id=req.requisition_id,
        job_title=req.job_title,
        department=req.department,
        seniority_level=req.seniority_level,
        employment_type=req.employment_type,
        location_city=req.location_city,
        location_country=req.location_country,
        remote_available=req.remote_available or False,
        min_years_experience=req.min_years_experience,
        max_years_experience=req.max_years_experience,
        min_education_level=req.min_education_level,
        full_job_description=req.full_job_description,
        contact_email=req.contact_email,
        # ISO 8601 string so the frontend can parse/display it directly
        cv_collection_end_date=(
            req.cv_collection_end_date.isoformat()
            if req.cv_collection_end_date else None
        ),
        is_open=_is_open(req),
    )

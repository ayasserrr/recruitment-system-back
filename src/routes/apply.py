"""
Public (no-auth) application endpoints.

POST /api/v1/apply/{jobId}                  — submit a job application with CV upload
GET  /api/v1/candidates/my-applications     — candidate's own applications, lookup by email
GET  /api/v1/public/jobs/{jobId}            — public job detail for the apply page
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.schemas.frontend_schemas import ApplyResponse, CandidateApplicationItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["apply"])

_UPLOAD_DIR = "uploads/cvs"
os.makedirs(_UPLOAD_DIR, exist_ok=True)

_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}


# ── GET /api/v1/public/jobs/{jobId} (candidate-facing job detail) ──────────────

@router.get("/api/v1/public/jobs/{job_id}", summary="Public job detail for apply page")
def public_job_detail(job_id: int, db: Session = Depends(get_db)):
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == job_id)
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    company_name = jr.company.name if jr.company else "Our Company"
    return {
        "id": jr.requisition_id,
        "jobTitle": jr.job_title,
        "department": jr.department,
        "seniorityLevel": jr.seniority_level,
        "employmentType": jr.employment_type,
        "city": jr.location_city,
        "country": jr.location_country,
        "remoteAvailable": jr.remote_available or False,
        "fullDescription": jr.full_job_description,
        "minExperience": jr.min_years_experience,
        "maxExperience": jr.max_years_experience,
        "minEducation": jr.min_education_level,
        "contactEmail": jr.contact_email,
        "currency": jr.currency,
        "minSalary": float(jr.min_salary_monthly) if jr.min_salary_monthly else None,
        "maxSalary": float(jr.max_salary_monthly) if jr.max_salary_monthly else None,
        "deadline": jr.application_deadline.isoformat() if jr.application_deadline else None,
        "postingStartDate": jr.posting_start_date.isoformat() if jr.posting_start_date else None,
        "postingEndDate": jr.cv_collection_end_date.isoformat() if jr.cv_collection_end_date else None,
        "company": company_name,
        "isOpen": jr.status not in ("ranking_complete", "Closed"),
    }


# ── POST /api/v1/apply/{jobId} ─────────────────────────────────────────────────

@router.post(
    "/api/v1/apply/{job_id}",
    response_model=ApplyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a job application (no auth required)",
)
async def apply_for_job(
    job_id: int,
    fullName: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    cvFile: UploadFile = File(...),
    coverLetter: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    # ── Validate job ───────────────────────────────────────────────────────────
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == job_id)
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    posting: Optional[JobPosting] = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == job_id)
        .first()
    )
    if not posting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This job is not currently accepting applications.",
        )

    # ── Validate file extension ────────────────────────────────────────────────
    ext = os.path.splitext(cvFile.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CV must be a PDF, DOC, or DOCX file.",
        )

    # ── Find or create candidate ───────────────────────────────────────────────
    name_parts = fullName.strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    candidate: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.email == email.lower().strip()).first()
    )
    if not candidate:
        candidate = Candidate(
            first_name=first_name,
            last_name=last_name,
            email=email.lower().strip(),
            phone=phone,
        )
        db.add(candidate)
        db.flush()
    else:
        # Update phone if missing
        if not candidate.phone and phone:
            candidate.phone = phone

    # ── Check for duplicate application ───────────────────────────────────────
    existing_app: Optional[Application] = (
        db.query(Application)
        .filter(
            Application.posting_id == posting.posting_id,
            Application.candidate_id == candidate.candidate_id,
        )
        .first()
    )
    if existing_app:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already applied for this position.",
        )

    # ── Save CV file ───────────────────────────────────────────────────────────
    candidate_dir = os.path.join(_UPLOAD_DIR, str(candidate.candidate_id))
    os.makedirs(candidate_dir, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(candidate_dir, unique_name)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(cvFile.file, f)

    # ── Create CandidateCV record ──────────────────────────────────────────────
    cv_record = CandidateCV(
        candidate_id=candidate.candidate_id,
        file_url=file_path,
        file_name=cvFile.filename or unique_name,
        is_primary=True,
    )
    db.add(cv_record)
    db.flush()

    # ── Create Application ─────────────────────────────────────────────────────
    application = Application(
        posting_id=posting.posting_id,
        candidate_id=candidate.candidate_id,
        cv_id=cv_record.cv_id,
        status="Applied",
        cover_letter=coverLetter,
        current_pipeline_stage="Applied",
    )
    db.add(application)

    # Update total_applications counter on posting
    posting.total_applications = (posting.total_applications or 0) + 1

    db.commit()

    logger.info(
        "[apply] New application: candidate=%d posting=%d",
        candidate.candidate_id,
        posting.posting_id,
    )

    return ApplyResponse(
        applicationId=application.application_id,
        message="Application submitted successfully.",
    )


# ── GET /api/v1/candidates/my-applications ─────────────────────────────────────

@router.get(
    "/api/v1/candidates/my-applications",
    response_model=List[CandidateApplicationItem],
    summary="List all applications for a candidate by email (no auth required)",
)
def my_applications(
    email: str = Query(..., description="Candidate email address"),
    db: Session = Depends(get_db),
):
    candidate: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.email == email.lower().strip()).first()
    )
    if not candidate:
        return []

    apps: List[Application] = (
        db.query(Application)
        .filter(Application.candidate_id == candidate.candidate_id)
        .order_by(Application.applied_at.desc())
        .all()
    )

    result = []
    for app in apps:
        posting = app.posting
        if not posting:
            continue
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == posting.requisition_id)
            .first()
        )
        if not jr:
            continue
        company_name = jr.company.name if jr.company else "Our Company"
        result.append(
            CandidateApplicationItem(
                applicationId=app.application_id,
                jobTitle=jr.job_title,
                company=company_name,
                appliedDate=app.applied_at.strftime("%Y-%m-%d") if app.applied_at else "",
                status=app.status or "Applied",
            )
        )

    return result

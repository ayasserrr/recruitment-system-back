"""
Public (no-auth) application endpoints.

POST /api/v1/apply/{jobId}              — submit a job application with CV upload
GET  /api/v1/candidates/my-applications — candidate's own applications (lookup by email)
GET  /api/v1/public/jobs/{jobId}        — public job detail for the apply page

UPLOAD PIPELINE (POST /api/v1/apply)
─────────────────────────────────────
1. Validate job, posting, file extension.
2. Find-or-create Candidate row (flush, not commit).
3. Reject duplicate applications.
4. Save file to uploads/cvs/<candidate_id>/.
5. fitz_extract_text()  — local PyMuPDF extraction; fail fast if empty/unreadable.
6. parse_with_llm()     — GPT-4o-mini structures the fitz text into ParsedCV.
7. Create CandidateCV with extracted_text = raw fitz output (persisted immediately).
8. CVPersistenceService.persist_cv_sub_tables() — writes skills, experiences,
   educations, projects in the SAME transaction.
9. Update Candidate profile fields from ParsedCV.
10. Create Application row.
11. Single db.commit() — everything lands atomically.

Log: [FLOW-SYNC] CV {cv_id} parsed locally via fitz and persisted.
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
from services.cv_persistence_service import CVPersistenceService
from services.cv_service import extract_cv_info

logger = logging.getLogger(__name__)

router = APIRouter(tags=["apply"])

_UPLOAD_DIR = "uploads/cvs"
os.makedirs(_UPLOAD_DIR, exist_ok=True)

_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}


# ── GET /api/v1/public/jobs/{jobId} (candidate-facing job detail) ──────────────

@router.get("/api/v1/public/jobs/{job_id}", summary="Public job detail for apply page")
def public_job_detail(job_id: int, db: Session = Depends(get_db)):
    from datetime import date as _date
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == job_id)
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    try:
        company_name = jr.company.name if jr.company else "Our Company"
    except Exception:
        company_name = "Our Company"

    end_date_passed = bool(
        jr.cv_collection_end_date and jr.cv_collection_end_date < _date.today()
    )
    is_open = (
        jr.status not in ("ranking_complete", "Closed")
        and not end_date_passed
    )

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
        "isOpen": is_open,
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
    # ── 1. Validate job ─────────────────────────────────────────────────────────
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

    # ── 2. Validate file extension ──────────────────────────────────────────────
    ext = os.path.splitext(cvFile.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CV must be a PDF, DOC, or DOCX file.",
        )

    # ── 3. Find or create Candidate ─────────────────────────────────────────────
    name_parts = fullName.strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""
    clean_email = email.lower().strip()

    candidate: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.email == clean_email).first()
    )
    if not candidate:
        candidate = Candidate(
            first_name=first_name,
            last_name=last_name,
            email=clean_email,
            phone=phone,
        )
        db.add(candidate)
        db.flush()
    else:
        if not candidate.phone and phone:
            candidate.phone = phone

    # ── 4. Reject duplicate applications ───────────────────────────────────────
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

    # ── 5. Save file to disk ────────────────────────────────────────────────────
    candidate_dir = os.path.join(_UPLOAD_DIR, str(candidate.candidate_id))
    os.makedirs(candidate_dir, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(candidate_dir, unique_name)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(cvFile.file, f)

    # ── 6 & 7. fitz extraction + LLM structuring (must happen before DB writes) ─
    raw_text: str = ""
    parsed_cv = None
    cv_parse_error: Optional[str] = None

    try:
        raw_text, parsed_cv = extract_cv_info(
            file_path=file_path,
            ext=ext,
            cv_id=None,           # cv_id not yet assigned
            candidate_email_hint=clean_email,
        )
    except ValueError as ve:
        # File is empty / unreadable — reject the application immediately
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ve),
        )
    except Exception as exc:
        # Non-fatal: log and continue without parsed data
        cv_parse_error = str(exc)
        logger.warning(
            "[apply] Non-fatal CV parsing error for candidate=%d: %s",
            candidate.candidate_id, exc,
        )

    # ── 8. Create CandidateCV (with extracted_text stored immediately) ──────────
    cv_record = CandidateCV(
        candidate_id=candidate.candidate_id,
        file_url=file_path,
        file_name=cvFile.filename or unique_name,
        is_primary=True,
        extracted_text=raw_text or None,
    )
    db.add(cv_record)
    db.flush()  # get cv_id before sub-table writes

    # ── 9. Persist sub-tables via CVPersistenceService ──────────────────────────
    if parsed_cv is not None:
        try:
            svc = CVPersistenceService()
            svc.persist_cv_sub_tables(parsed_cv, db, cv_record.cv_id)

            # Update Candidate profile (only overwrite empty fields)
            if parsed_cv.professional_summary and not candidate.professional_summary:
                candidate.professional_summary = parsed_cv.professional_summary
            if parsed_cv.years_of_experience and not candidate.years_of_experience:
                candidate.years_of_experience = parsed_cv.years_of_experience
            if parsed_cv.education_level and not candidate.education_level:
                candidate.education_level = parsed_cv.education_level
            if parsed_cv.field_of_study and not candidate.field_of_study:
                candidate.field_of_study = parsed_cv.field_of_study

            logger.info(
                "[FLOW-SYNC] CV %d parsed locally via fitz and persisted. "
                "candidate=%d  skills=%d  experiences=%d  projects=%d",
                cv_record.cv_id,
                candidate.candidate_id,
                len(parsed_cv.skills),
                len(parsed_cv.experiences),
                len(parsed_cv.projects),
            )
        except Exception as persist_exc:
            # Sub-table persistence failure is non-fatal; ranking has raw_text fallback
            logger.warning(
                "[apply] Sub-table persistence error for cv_id=%d: %s",
                cv_record.cv_id, persist_exc,
            )

    # ── 10. Create Application ──────────────────────────────────────────────────
    application = Application(
        posting_id=posting.posting_id,
        candidate_id=candidate.candidate_id,
        cv_id=cv_record.cv_id,
        status="Applied",
        cover_letter=coverLetter,
        current_pipeline_stage="Applied",
    )
    db.add(application)
    posting.total_applications = (posting.total_applications or 0) + 1

    # ── 11. Single atomic commit ────────────────────────────────────────────────
    db.commit()

    logger.info(
        "[apply] Application submitted: candidate=%d posting=%d cv=%d parse_error=%s",
        candidate.candidate_id,
        posting.posting_id,
        cv_record.cv_id,
        cv_parse_error or "none",
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

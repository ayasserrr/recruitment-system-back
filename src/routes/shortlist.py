"""
Company-wide shortlist endpoints.

GET    /api/v1/shortlist                  — all shortlisted candidates for the company
DELETE /api/v1/shortlist/{candidateId}   — remove a candidate from the shortlist
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.semantic_matched_skill import SemanticMatchedSkill
from models.db.shortlisted_candidate import ShortlistedCandidate
from models.schemas.frontend_schemas import (
    AddToShortlistRequest,
    AddToShortlistResponse,
    ShortlistItem,
    ShortlistListResponse,
    match_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/shortlist", tags=["shortlist"])


# ── GET /api/v1/shortlist ─────────────────────────────────────────────────────

@router.get("", response_model=ShortlistListResponse, summary="All shortlisted candidates")
def list_shortlist(
    job_id: Optional[int] = Query(None, description="Filter by job ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    company_id = ctx["company_id"]

    # Base query: shortlisted entries belonging to this company
    base_q = (
        db.query(ShortlistedCandidate)
        .join(JobPosting, JobPosting.posting_id == ShortlistedCandidate.posting_id)
        .join(JobRequisition, JobRequisition.requisition_id == JobPosting.requisition_id)
        .filter(JobRequisition.company_id == company_id)
    )

    if job_id is not None:
        base_q = base_q.filter(
            JobPosting.requisition_id == job_id
        )

    total = base_q.count()
    entries: List[ShortlistedCandidate] = (
        base_q.order_by(ShortlistedCandidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items: List[ShortlistItem] = []
    for entry in entries:
        cand: Optional[Candidate] = (
            db.query(Candidate)
            .filter(Candidate.candidate_id == entry.candidate_id)
            .first()
        )
        if not cand:
            continue

        app: Optional[Application] = (
            db.query(Application)
            .filter(Application.application_id == entry.application_id)
            .first()
        )

        # Job title
        posting = db.query(JobPosting).filter(JobPosting.posting_id == entry.posting_id).first()
        jr: Optional[JobRequisition] = None
        if posting:
            jr = db.query(JobRequisition).filter(
                JobRequisition.requisition_id == posting.requisition_id
            ).first()

        job_title = jr.job_title if jr else "Unknown"

        # Semantic score for match label
        score: Optional[float] = None
        match: Optional[str] = None
        if app:
            report: Optional[SemanticAnalysisReport] = (
                db.query(SemanticAnalysisReport)
                .filter(SemanticAnalysisReport.application_id == app.application_id)
                .first()
            )
            if report and report.match_percentage is not None:
                score = float(report.match_percentage)
                match = match_label(score)

        # Skills
        skills: List[str] = []
        if app and app.cv_id:
            skills = [
                s.skill_name
                for s in db.query(CVSkill)
                .filter(CVSkill.cv_id == app.cv_id)
                .limit(10)
                .all()
            ]

        # Projects
        projects: List[str] = []
        if app and app.cv_id:
            projects = [
                p.project_name
                for p in db.query(CVProject)
                .filter(CVProject.cv_id == app.cv_id)
                .limit(5)
                .all()
                if p.project_name
            ]

        # Education / experience
        education_str: Optional[str] = None
        if cand.education_level:
            education_str = cand.education_level
            if cand.field_of_study:
                education_str += f" in {cand.field_of_study}"

        exp_label: Optional[str] = None
        if cand.years_of_experience:
            exp_label = f"{cand.years_of_experience}+ years"

        items.append(
            ShortlistItem(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                email=cand.email,
                phone=cand.phone,
                experience=exp_label,
                education=education_str,
                summary=cand.professional_summary,
                projects=projects,
                score=round(score, 1) if score is not None else None,
                match=match,
                skills=skills,
                jobTitle=job_title,
                shortlistedFrom=entry.shortlisted_from or "Final Ranking",
                shortlistedDate=entry.created_at.strftime("%Y-%m-%d") if entry.created_at else "",
                shortlistNote=entry.shortlist_note,
            )
        )

    return ShortlistListResponse(count=total, results=items)


# ── POST /api/v1/shortlist ────────────────────────────────────────────────────

@router.post("", response_model=AddToShortlistResponse, status_code=201, summary="Add candidate to shortlist")
def add_to_shortlist(
    body: AddToShortlistRequest,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    company_id = ctx["company_id"]

    # Verify the job belongs to this company
    jr = (
        db.query(JobRequisition)
        .filter(
            JobRequisition.requisition_id == body.jobId,
            JobRequisition.company_id == company_id,
        )
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    posting = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == body.jobId)
        .first()
    )
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
            detail="Candidate application not found for this job.",
        )

    existing: Optional[ShortlistedCandidate] = (
        db.query(ShortlistedCandidate)
        .filter(ShortlistedCandidate.application_id == app.application_id)
        .first()
    )
    if existing:
        existing.shortlist_note = body.note
        existing.shortlisted_from = body.shortlistedFrom
        db.commit()
        return AddToShortlistResponse(id=existing.id, status="Shortlisted")

    entry = ShortlistedCandidate(
        application_id=app.application_id,
        posting_id=posting.posting_id,
        candidate_id=body.candidateId,
        shortlisted_from=body.shortlistedFrom,
        shortlist_note=body.note,
    )
    db.add(entry)
    app.status = "Shortlisted"
    db.commit()
    db.refresh(entry)

    return AddToShortlistResponse(id=entry.id, status="Shortlisted")


# ── DELETE /api/v1/shortlist/{candidateId} ────────────────────────────────────

@router.delete(
    "/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a candidate from the shortlist",
)
def remove_from_shortlist(
    candidate_id: int,
    job_id: Optional[int] = Query(None, description="Scope removal to a specific job"),
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    company_id = ctx["company_id"]

    q = (
        db.query(ShortlistedCandidate)
        .join(JobPosting, JobPosting.posting_id == ShortlistedCandidate.posting_id)
        .join(JobRequisition, JobRequisition.requisition_id == JobPosting.requisition_id)
        .filter(
            ShortlistedCandidate.candidate_id == candidate_id,
            JobRequisition.company_id == company_id,
        )
    )
    if job_id is not None:
        q = q.filter(JobPosting.requisition_id == job_id)

    entries = q.all()
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found in shortlist.",
        )

    for entry in entries:
        # Revert application status to Shortlisted → previous stage
        app: Optional[Application] = (
            db.query(Application)
            .filter(Application.application_id == entry.application_id)
            .first()
        )
        if app and app.status == "Shortlisted":
            app.status = "Applied"
        db.delete(entry)

    db.commit()

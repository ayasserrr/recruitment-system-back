"""
Final Ranking endpoints.

GET  /api/v1/jobs/{jobId}/final-ranking                              — ranked list
POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist      — shortlist candidate
POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer          — send offer email
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.final_ranking import FinalRanking
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.shortlisted_candidate import ShortlistedCandidate
from models.schemas.frontend_schemas import (
    FinalRankingItem,
    SendOfferRequest,
    ShortlistRequest,
    ShortlistResponse,
    match_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["final-ranking"])

# Score weights: semantic 20%, assessment 30%, technical 30%, HR 20%
_W_SEMANTIC = 0.20
_W_ASSESSMENT = 0.30
_W_TECHNICAL = 0.30
_W_HR = 0.20


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


def _hire_probability(score: float, red_flag: bool) -> int:
    if red_flag:
        return max(0, int(score * 0.5))
    if score >= 90:
        return 95
    if score >= 80:
        return 85
    if score >= 70:
        return 70
    if score >= 55:
        return 50
    return max(0, int(score * 0.5))


def _recommendation_label(score: float, red_flag: bool) -> str:
    if red_flag:
        return "No Hire"
    if score >= 90:
        return "Top Candidate"
    if score >= 80:
        return "Strong Hire"
    if score >= 65:
        return "Hire"
    if score >= 50:
        return "Maybe"
    return "No Hire"


# ── GET /api/v1/jobs/{jobId}/final-ranking ────────────────────────────────────

@router.get(
    "/{job_id}/final-ranking",
    response_model=List[FinalRankingItem],
    summary="Final ranked candidate list",
)
def get_final_ranking(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)
    if not posting:
        return []

    rows = (
        db.query(FinalRanking, Application, Candidate)
        .join(Application, Application.application_id == FinalRanking.application_id)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .filter(FinalRanking.posting_id == posting.posting_id)
        .order_by(FinalRanking.final_rank.asc())
        .all()
    )

    result: List[FinalRankingItem] = []
    for ranking, app, cand in rows:
        # Use weighted_total_score from DB (computed by the pipeline) as the primary score
        overall = float(ranking.weighted_total_score or 0)

        # Recompute using frontend weights if sub-scores exist
        sem = float(ranking.semantic_score or 0)
        asm = float(ranking.assessment_score or 0)
        tech = float(ranking.technical_interview_score or 0)
        hr = float(ranking.hr_interview_score or 0)

        # If we have all sub-scores, reweight for frontend display
        if sem or asm or tech or hr:
            parts = []
            weights_used = 0.0
            if sem:
                parts.append(sem * _W_SEMANTIC)
                weights_used += _W_SEMANTIC
            if asm:
                parts.append(asm * _W_ASSESSMENT)
                weights_used += _W_ASSESSMENT
            if tech:
                # tech interview score is on 0–10 scale; normalise to 0–100
                parts.append((tech * 10) * _W_TECHNICAL)
                weights_used += _W_TECHNICAL
            if hr:
                parts.append((hr * 10) * _W_HR)
                weights_used += _W_HR
            if weights_used > 0 and parts:
                overall = round(sum(parts) / weights_used, 2)

        red_flag = ranking.red_flag or False

        # Application status
        app_status = app.status or "Applied"
        shortlisted = (
            db.query(ShortlistedCandidate)
            .filter(ShortlistedCandidate.application_id == app.application_id)
            .first()
        )
        if shortlisted:
            app_status = "Shortlisted"

        result.append(
            FinalRankingItem(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                email=cand.email,
                overallScore=round(overall, 2),
                semanticScore=round(sem, 2) if sem else None,
                assessmentScore=round(asm, 2) if asm else None,
                technicalScore=round(float(ranking.technical_interview_score or 0), 2) or None,
                cultureFitScore=round(float(ranking.hr_interview_score or 0), 2) or None,
                recommendation=_recommendation_label(overall, red_flag),
                hireProbability=_hire_probability(overall, red_flag),
                applicationStatus=app_status,
            )
        )

    return result


# ── POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist ───────────

@router.post(
    "/{job_id}/final-ranking/{candidate_id}/shortlist",
    response_model=ShortlistResponse,
    summary="Shortlist a candidate from the final ranking",
)
def shortlist_candidate(
    job_id: int,
    candidate_id: int,
    body: ShortlistRequest,
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate application not found.",
        )

    existing: Optional[ShortlistedCandidate] = (
        db.query(ShortlistedCandidate)
        .filter(ShortlistedCandidate.application_id == app.application_id)
        .first()
    )
    if existing:
        existing.shortlist_note = body.note
        db.commit()
        return ShortlistResponse(status="Shortlisted")

    entry = ShortlistedCandidate(
        application_id=app.application_id,
        posting_id=posting.posting_id,
        candidate_id=candidate_id,
        shortlisted_from="Final Ranking",
        shortlist_note=body.note,
    )
    db.add(entry)
    app.status = "Shortlisted"
    db.commit()

    return ShortlistResponse(status="Shortlisted")


# ── POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer ───────────────

@router.post(
    "/{job_id}/final-ranking/{candidate_id}/offer",
    summary="Send an offer to a candidate",
)
def send_offer(
    job_id: int,
    candidate_id: int,
    body: SendOfferRequest,
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate application not found.",
        )

    cand: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.candidate_id == candidate_id).first()
    )
    if not cand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")

    # Update application status
    app.status = "Offer Extended"
    db.commit()

    # Send offer email
    email_sent = False
    try:
        from services.email_service import send_offer_email_sync
        email_sent = send_offer_email_sync(
            recipient_email=cand.email,
            first_name=cand.first_name or "Candidate",
            position=body.position,
            salary=body.salary,
            start_date=body.startDate,
            department=body.department,
            reporting_to=body.reportingTo,
            benefits=body.benefits,
            contract_type=body.contractType,
            location=body.location,
            notes=body.notes,
            company_name=jr.company.name if jr.company else "Our Company",
        )
    except Exception:
        logger.warning(
            "[offer] Email service unavailable — status updated but email not sent. "
            "Candidate %d, JR %d.",
            candidate_id, job_id,
        )

    return {
        "message": "Offer extended." + (" Email sent." if email_sent else " Email service unavailable."),
        "candidateId": candidate_id,
        "applicationStatus": "Offer Extended",
    }

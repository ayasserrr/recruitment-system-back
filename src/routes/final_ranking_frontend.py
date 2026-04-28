"""
Final Ranking endpoints — 4-Stage Pipeline (Screening 20% / Assessment 25% / Tech 30% / HR 25%).

GET  /api/v1/jobs/{jobId}/final-ranking                              — ranked list
GET  /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report   — per-candidate SHAP detail
POST /api/v1/jobs/{jobId}/trigger-ranking                           — trigger pipeline run
POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist     — shortlist candidate
POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer         — send offer email
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
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
    SHAPReportResponse,
    SendOfferRequest,
    ShortlistRequest,
    ShortlistResponse,
    TriggerRankingResponse,
    match_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["final-ranking"])


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
    summary="Final ranked candidate list (4-stage: Screening 20% / Assessment 25% / Tech 30% / HR 25%)",
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
        # Use the pipeline-computed weighted_total_score — do NOT re-weight here.
        # The backend pipeline (score_candidates_node) applies 20/25/30/25 correctly
        # and redistributes proportionally when any stage is absent.
        overall = float(ranking.weighted_total_score or 0)
        red_flag = bool(ranking.red_flag)

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
                finalRank=ranking.final_rank,
                overallScore=round(overall, 2),
                # All phase scores stored as 0-100 by the pipeline — no ×10 needed
                semanticScore=round(float(ranking.semantic_score), 2) if ranking.semantic_score is not None else None,
                assessmentScore=round(float(ranking.assessment_score), 2) if ranking.assessment_score is not None else None,
                technicalScore=round(float(ranking.technical_interview_score), 2) if ranking.technical_interview_score is not None else None,
                hrScore=round(float(ranking.hr_interview_score), 2) if ranking.hr_interview_score is not None else None,
                recommendation=_recommendation_label(overall, red_flag),
                hireProbability=_hire_probability(overall, red_flag),
                applicationStatus=app_status,
                redFlag=red_flag,
                redFlagReason=ranking.red_flag_reason,
                shapSummary=ranking.shap_summary,
            )
        )

    return result


# ── GET /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report ──────────

@router.get(
    "/{job_id}/final-ranking/{candidate_id}/shap-report",
    response_model=SHAPReportResponse,
    summary="Cross-phase SHAP explainability report for a single candidate",
)
def get_candidate_shap_report(
    job_id: int,
    candidate_id: int,
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

    ranking: Optional[FinalRanking] = (
        db.query(FinalRanking)
        .filter(FinalRanking.application_id == app.application_id)
        .first()
    )
    if not ranking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Final ranking not computed yet for this candidate.")

    cand: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.candidate_id == candidate_id).first()
    )

    # Parse SHAP JSON: {"screening": φ, "assessment": φ, "tech_interview": φ, "hr_interview": φ}
    shap_data: dict = {}
    if ranking.shap_json:
        try:
            shap_data = json.loads(ranking.shap_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return SHAPReportResponse(
        candidateId=candidate_id,
        candidateName=f"{cand.first_name} {cand.last_name}".strip() if cand else "Unknown",
        overallScore=round(float(ranking.weighted_total_score or 0), 2),
        finalRank=ranking.final_rank,
        shapScreening=shap_data.get("screening"),
        shapAssessment=shap_data.get("assessment"),
        shapTechInterview=shap_data.get("tech_interview"),
        shapHrInterview=shap_data.get("hr_interview"),
        shapSummary=ranking.shap_summary,
        # Weights are stored implicitly in the SHAP values; expose the defaults for display
        weightScreening=0.20,
        weightAssessment=0.25,
        weightTechInterview=0.30,
        weightHrInterview=0.25,
        redFlag=bool(ranking.red_flag),
        redFlagReason=ranking.red_flag_reason,
    )


# ── POST /api/v1/jobs/{jobId}/trigger-ranking ─────────────────────────────────

@router.post(
    "/{job_id}/trigger-ranking",
    response_model=TriggerRankingResponse,
    summary="Trigger the 8-node Final Ranking pipeline for this job",
)
def trigger_final_ranking(
    job_id: int,
    background_tasks: BackgroundTasks,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_jr_posting(job_id, ctx["company_id"], db)

    if jr.processing_status == "processing":
        return TriggerRankingResponse(
            message="Ranking pipeline is already running for this job.",
            requisitionId=job_id,
            status="already_running",
        )

    def _run_ranking(requisition_id: int):
        try:
            from services.ranking_service import compute_final_rankings
            compute_final_rankings(requisition_id)
        except Exception as exc:
            logger.error("[trigger-ranking] Pipeline error for JR %d: %s", requisition_id, exc)

    background_tasks.add_task(_run_ranking, job_id)
    logger.info("[trigger-ranking] Queued final ranking for JR %d.", job_id)

    return TriggerRankingResponse(
        message="Final ranking pipeline started in background.",
        requisitionId=job_id,
        status="started",
    )


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate application not found.")

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
    summary="Send a job offer to a candidate",
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate application not found.")

    cand: Optional[Candidate] = (
        db.query(Candidate).filter(Candidate.candidate_id == candidate_id).first()
    )
    if not cand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")

    app.status = "Offer Extended"
    db.commit()

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
        logger.warning("[offer] Email service unavailable for candidate %d JR %d.", candidate_id, job_id)

    return {
        "message": "Offer extended." + (" Email sent." if email_sent else " Email service unavailable."),
        "candidateId": candidate_id,
        "applicationStatus": "Offer Extended",
    }

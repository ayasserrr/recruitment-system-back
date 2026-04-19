from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from models.db.job_requisition import JobRequisition
from services.ranking_graph import run_ranking_graph

logger = logging.getLogger(__name__)

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
    posting_start_date: Optional[str]       # ISO 8601 — frontend uses this to show "opens on"
    cv_collection_end_date: Optional[str]   # ISO 8601 — frontend uses this to show "closes on"
    is_open: bool

    class Config:
        from_attributes = True


# ── Shared deadline utility ────────────────────────────────────────────────

def check_job_deadline(requisition_id: int, db: Session) -> JobRequisition:
    """
    Fetch the requisition and enforce the application window:
      • 404  if the job does not exist.
      • 403 NOT_YET_OPEN   if today < posting_start_date  (job not open yet).
      • 403 APPLICATION_CLOSED  if today >= cv_collection_end_date (deadline passed).
    """
    req = db.query(JobRequisition).filter(
        JobRequisition.requisition_id == requisition_id
    ).first()

    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    today = datetime.now().date()

    # Opening gate: reject submissions before the job is officially live
    if req.posting_start_date is not None and today < req.posting_start_date:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NOT_YET_OPEN",
        )

    # Closing gate: reject submissions once the CV collection deadline has passed
    if req.cv_collection_end_date is not None and today > req.cv_collection_end_date:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="APPLICATION_CLOSED",
        )

    return req


def _is_open(req: JobRequisition) -> bool:
    """
    True only when today is inside the application window:
      posting_start_date <= today < cv_collection_end_date
    If a date is not set it is treated as unbounded on that side.
    """
    today = datetime.now().date()
    if req.posting_start_date is not None and today < req.posting_start_date:
        return False   # job hasn't opened yet
    if req.cv_collection_end_date is None:
        return True    # no closing deadline → open indefinitely
    return today <= req.cv_collection_end_date


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
        posting_start_date=(
            req.posting_start_date.isoformat()
            if req.posting_start_date else None
        ),
        cv_collection_end_date=(
            req.cv_collection_end_date.isoformat()
            if req.cv_collection_end_date else None
        ),
        is_open=_is_open(req),
    )


# ── POST /api/v1/jobs/{jid}/rank-candidates ────────────────────────────────
#
# Triggers the AI Recruitment Intelligence v6.0 LangGraph workflow.
# By default the endpoint requires the CV collection deadline to have passed.
# Pass ?force=true to override (e.g. for testing before the deadline).

@jobs_router.post(
    "/{jid}/rank-candidates",
    summary="Run AI candidate ranking for a job (after CV collection deadline)",
    response_class=JSONResponse,
)
def rank_candidates(
    jid: int,
    force: bool = Query(
        default=False,
        description="Skip deadline check — run ranking even if CV collection is still open.",
    ),
    db: Session = Depends(get_db),
):
    """
    Executes the 6-node LangGraph ranking pipeline for requisition ``jid``:

    1. **Context Gatherer** — loads JD + all candidate CV data from the DB.
    2. **Deterministic Scoring** — rule-based experience / education / skills / teamwork.
    3. **LLM Qualitative** — Groq Llama-3.3-70b: project depth, strengths, concerns.
    4. **GenAI Validator** — detects RAG / LLM / MLOps evidence with deployment context.
    5. **Final Ranker** — dynamic weights (Student vs Experience mode), final score 0–100.
    6. **Persistence** — upserts results to ``semantic_analysis_reports`` table.

    Returns a **Summary Table** and **Detailed Recruiter Reports** for every candidate.
    """
    # ── Validate job exists ────────────────────────────────────────────────
    req: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == jid)
        .first()
    )
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    # ── Deadline guard (default: ranking only makes sense after deadline) ──
    if not force and req.cv_collection_end_date is not None:
        today = datetime.now().date()
        if today < req.cv_collection_end_date:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"CV collection is still open until {req.cv_collection_end_date}. "
                    "Use ?force=true to rank before the deadline."
                ),
            )

    # ── Run the LangGraph workflow (synchronous — runs in the request thread) ──
    logger.info("[rank_candidates route] Triggering ranking graph for requisition %d.", jid)
    try:
        final_state = run_ranking_graph(requisition_id=jid)
    except Exception as exc:
        logger.exception("[rank_candidates route] Unhandled error during graph execution.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ranking workflow failed: {exc}",
        )

    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=final_state["error"],
        )

    ranked: list[dict] = final_state.get("ranked_candidates", [])
    jd_data: dict = final_state.get("jd_data", {})

    if not ranked:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "completed",
                "requisition_id": jid,
                "job_title": req.job_title,
                "total_candidates": 0,
                "scoring_mode": "N/A",
                "message": "No applications found for this job.",
                "summary_table": [],
                "detailed_reports": [],
            },
        )

    # ── Build Summary Table ────────────────────────────────────────────────
    summary_table = [
        {
            "rank": c["rank_in_pool"],
            "candidate_name": c.get("candidate_name"),
            "email": c.get("email"),
            "application_id": c.get("application_id"),
            "final_score": c.get("final_score"),
            "recommendation": c.get("recommendation"),
        }
        for c in ranked
    ]

    # ── Build Detailed Recruiter Reports ───────────────────────────────────
    detailed_reports = []
    for c in ranked:
        det = c.get("det_scores", {})
        llm = c.get("llm_scores", {})
        genai = c.get("genai_data", {})
        comps = c.get("component_scores", {})

        detailed_reports.append({
            # ── Pool position ──────────────────────────────────────────────
            "rank": c.get("rank_in_pool"),
            "total_in_pool": c.get("total_in_pool"),
            "score_gap_to_top": c.get("score_gap_to_top"),
            "top_score_in_pool": c.get("top_score"),
            # ── Identity ───────────────────────────────────────────────────
            "application_id": c.get("application_id"),
            "candidate_name": c.get("candidate_name"),
            "email": c.get("email"),
            # ── Scoring ────────────────────────────────────────────────────
            "final_score": c.get("final_score"),
            "weighted_sum": c.get("weighted_sum"),
            "genai_bonus": c.get("genai_bonus_applied", 0),
            "scoring_mode": c.get("scoring_mode"),
            # Pool-relative label (Top Candidate / Strong Runner-Up / Hire…)
            "recommendation": c.get("recommendation"),
            # Absolute threshold tier (Strong Hire / Hire / Maybe…)
            "absolute_tier": c.get("absolute_tier"),
            "score_breakdown": {
                "experience_score": det.get("experience_score"),
                "education_score": det.get("education_score"),
                "skill_coverage": det.get("skill_coverage"),
                "teamwork_score": det.get("teamwork_score"),
                "project_depth": llm.get("project_depth"),
                "genai_bonus": genai.get("bonus", 0),
            },
            "mode_weights": c.get("weight_breakdown", {}),
            # ── Skills ────────────────────────────────────────────────────
            "matched_skills": c.get("matched_skills", []),
            "genai_evidence": genai.get("evidence", []),
            "genai_context": genai.get("context", "none"),
            # ── LLM output ────────────────────────────────────────────────
            "strengths": llm.get("strengths", []),
            "concerns": llm.get("concerns", []),
            "interview_questions": llm.get("interview_questions", []),
            "llm_summary": llm.get("llm_summary", ""),
            "llm_failed": c.get("llm_failed", False),
            # ── Fixes & experience ─────────────────────────────────────────
            "applied_fixes": c.get("applied_fixes", []),
            "experience": {
                "total_months": c.get("total_experience_months", 0),
                "internship_months": c.get("internship_months", 0),
                "ai_background_detected": c.get("ai_background_detected", False),
            },
        })

    scoring_mode = ranked[0].get("scoring_mode", "N/A") if ranked else "N/A"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "completed",
            "requisition_id": jid,
            "job_title": jd_data.get("job_title", req.job_title),
            "total_candidates": len(ranked),
            "scoring_mode": scoring_mode,
            "summary_table": summary_table,
            "detailed_reports": detailed_reports,
        },
    )

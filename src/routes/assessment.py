"""
Assessment API
──────────────
GET  /api/v1/assessment/{assessment_id}
     Returns questions (without answers) and marks the assessment In Progress.
     Requires ?token= from the invitation email.

POST /api/v1/assessment/{assessment_id}/submit
     Accepts candidate answers, runs the 4-node LangGraph AI-Grader pipeline
     (load_submission → ai_grader → aggregate_scores → generate_report),
     then dispatches a background Celery task to recalculate rank_in_pool
     across all candidates for the same requisition.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from database.connection import SessionLocal
from models.db.assessment_answer import AssessmentAnswer
from models.db.assessment_template_question import AssessmentTemplateQuestion
from models.db.candidate_assessment import CandidateAssessment
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from models.db.application import Application
from models.db.job_posting import JobPosting
from models.schemas.assessment_schema import (
    AnswerResult,
    AssessmentReportResponse,
    AssessmentSubmitRequest,
    AssessmentSubmitResponse,
)
from services.assessment_graph import verify_assessment_token
from services.grading_graph import run_grading_graph


def _check_deadline(assessment: CandidateAssessment, db) -> None:
    """
    Raises HTTP 403 with error code ASSESSMENT_EXPIRED if the global
    assessment deadline (stored on TechnicalAssessmentConfig) has passed.
    Called from both GET (serve questions) and POST (submit).
    """
    if not assessment.config_id:
        return
    config: Optional[TechnicalAssessmentConfig] = (
        db.query(TechnicalAssessmentConfig)
        .filter(TechnicalAssessmentConfig.config_id == assessment.config_id)
        .first()
    )
    if config and config.assessment_deadline:
        if datetime.utcnow() > config.assessment_deadline:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ASSESSMENT_EXPIRED",
                    "message": (
                        f"This assessment closed on "
                        f"{config.assessment_deadline.strftime('%Y-%m-%d %H:%M')} UTC. "
                        f"Submissions are no longer accepted."
                    ),
                },
            )

logger = logging.getLogger(__name__)

assessment_router = APIRouter(
    prefix="/api/v1/assessment",
    tags=["assessment"],
)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/assessment/{assessment_id} — Serve questions to candidate
# ─────────────────────────────────────────────────────────────────────────────

@assessment_router.get("/{assessment_id}")
def get_assessment(
    assessment_id: int,
    token: str = Query(..., description="HMAC security token from the invitation email"),
):
    """
    Returns the assessment questions for the candidate to answer.
    Strips correct_answer / grading_guide from the response.
    Transitions status Pending → In Progress on first open.
    """
    if not verify_assessment_token(assessment_id, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired assessment link.",
        )

    db = SessionLocal()
    try:
        assessment: Optional[CandidateAssessment] = (
            db.query(CandidateAssessment)
            .filter(CandidateAssessment.assessment_id == assessment_id)
            .first()
        )
        if not assessment:
            raise HTTPException(status_code=404, detail="Assessment not found.")
        if assessment.status == "Submitted":
            raise HTTPException(status_code=409, detail="Assessment already submitted.")
        if not assessment.template_id:
            raise HTTPException(status_code=404, detail="No questions configured for this assessment.")

        _check_deadline(assessment, db)

        questions = (
            db.query(AssessmentTemplateQuestion)
            .filter(AssessmentTemplateQuestion.template_id == assessment.template_id)
            .all()
        )

        # Fetch time limit from config for the frontend countdown timer
        time_limit = None
        try:
            from models.db.technical_assessment_config import TechnicalAssessmentConfig
            cfg = (
                db.query(TechnicalAssessmentConfig)
                .filter(TechnicalAssessmentConfig.config_id == assessment.config_id)
                .first()
            )
            if cfg:
                time_limit = cfg.time_limit_minutes
        except Exception:
            pass

        if assessment.status == "Pending":
            assessment.status = "In Progress"
            assessment.started_at = datetime.utcnow()
            db.commit()

        return JSONResponse(
            status_code=200,
            content={
                "assessment_id": assessment_id,
                "status": assessment.status,
                "time_limit_minutes": time_limit,
                "questions": [
                    {
                        "question_id": q.question_id,
                        "question_text": q.question_text,
                        "question_type": q.question_type,
                        "points": q.points,
                        "options": json.loads(q.options) if q.options else None,
                    }
                    for q in questions
                ],
            },
        )
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/assessment/{assessment_id}/submit — AI Grader pipeline
# ─────────────────────────────────────────────────────────────────────────────

@assessment_router.post("/{assessment_id}/submit", response_model=AssessmentSubmitResponse)
def submit_assessment(assessment_id: int, payload: AssessmentSubmitRequest):
    """
    Runs the full LangGraph AI-Grader pipeline:
      Node 1 load_submission   – validates assessment, loads questions
      Node 2 ai_grader         – MCQ exact-match / open-ended GPT-4o-mini
      Node 3 aggregate_scores  – sums scores, marks passed, saves AssessmentAnswers
      Node 4 generate_report   – GPT-4o-mini report → assessment_reports row

    After the graph completes, dispatches recalculate_assessment_rankings
    in the background so rank_in_pool is updated across all candidates.
    """
    if not verify_assessment_token(assessment_id, payload.token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired assessment link.",
        )

    # Enforce deadline before running the expensive grading pipeline
    _db = SessionLocal()
    try:
        _assessment = (
            _db.query(CandidateAssessment)
            .filter(CandidateAssessment.assessment_id == assessment_id)
            .first()
        )
        if _assessment:
            _check_deadline(_assessment, _db)
    finally:
        _db.close()

    # Convert payload to plain dicts for the graph
    submitted_answers = [
        {"question_id": a.question_id, "candidate_answer": a.candidate_answer}
        for a in payload.answers
    ]

    # ── Run the 4-node grading graph ──────────────────────────────────────────
    result = run_grading_graph(
        assessment_id=assessment_id,
        submitted_answers=submitted_answers,
    )

    if result.get("error"):
        # Map known domain errors to appropriate HTTP status codes
        err = result["error"]
        if "not found" in err.lower():
            raise HTTPException(status_code=404, detail=err)
        if "already submitted" in err.lower():
            raise HTTPException(status_code=409, detail=err)
        raise HTTPException(status_code=422, detail=err)

    # ── Dispatch background ranking recalculation ─────────────────────────────
    _dispatch_ranking(assessment_id)

    # ── Build response from graded answers ────────────────────────────────────
    db = SessionLocal()
    try:
        answer_rows = (
            db.query(AssessmentAnswer, AssessmentTemplateQuestion)
            .join(
                AssessmentTemplateQuestion,
                AssessmentAnswer.question_id == AssessmentTemplateQuestion.question_id,
            )
            .filter(AssessmentAnswer.assessment_id == assessment_id)
            .all()
        )
        answer_results = [
            AnswerResult(
                question_id=ans.question_id,
                question_text=q.question_text,
                question_type=q.question_type or "mcq",
                candidate_answer=ans.candidate_answer or "",
                score_awarded=float(ans.score_awarded or 0),
                max_points=q.points,
                ai_feedback=ans.ai_feedback or "",
            )
            for ans, q in answer_rows
        ]
    finally:
        db.close()

    report_response = None
    if result.get("report_id"):
        report_response = AssessmentReportResponse(
            report_id=result["report_id"],
            overall_score=result["total_score"],
            strengths=result["strengths"],
            weaknesses=result["weaknesses"],
            ai_feedback=result["ai_feedback"],
            recommendation=result["recommendation"],
        )

    logger.info(
        "[submit_assessment] Assessment %d complete — %.1f/%.0f (%.1f%%), passed=%s.",
        assessment_id,
        result["total_score"],
        result["total_possible"],
        result["score_pct"],
        result["passed"],
    )

    return AssessmentSubmitResponse(
        assessment_id=assessment_id,
        status="Submitted",
        total_score=result["total_score"],
        passing_score=result["passing_score"],
        passed=result["passed"],
        answers=answer_results,
        report=report_response,
    )


def _dispatch_ranking(assessment_id: int) -> None:
    """
    Look up the requisition_id for this assessment and fire
    recalculate_assessment_rankings in the background.
    Silently swallows errors so submission never fails because of this.
    """
    try:
        db = SessionLocal()
        try:
            assessment = (
                db.query(CandidateAssessment)
                .filter(CandidateAssessment.assessment_id == assessment_id)
                .first()
            )
            if not assessment:
                return
            app = (
                db.query(Application)
                .filter(Application.application_id == assessment.application_id)
                .first()
            )
            if not app:
                return
            posting = (
                db.query(JobPosting)
                .filter(JobPosting.posting_id == app.posting_id)
                .first()
            )
            if not posting:
                return
            requisition_id = posting.requisition_id
        finally:
            db.close()

        from tasks.assessment_tasks import recalculate_assessment_rankings
        recalculate_assessment_rankings.delay(requisition_id)
        logger.info(
            "[submit_assessment] Dispatched ranking recalculation for requisition %d.", requisition_id
        )
    except Exception as exc:
        logger.warning("[submit_assessment] Could not dispatch ranking task: %s", exc)

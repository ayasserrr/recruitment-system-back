"""
Assessment API
──────────────
GET  /api/v1/assessment/{assessment_id}
     Returns questions (without answers) and marks the assessment In Progress.
     Requires ?token= from the invitation email.

POST /api/v1/assessment/{assessment_id}/submit
     Saves candidate answers; MCQ is exact-matched immediately, open-ended answers
     are stored raw (score_awarded=NULL) and graded post-deadline by the relative
     grading pipeline (scan_and_dispatch_assessment_ranking → run_relative_grading).
     Returns immediately — no LLM call on the HTTP request thread.
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
from models.db.assessment_question_set import AssessmentQuestionSet
from models.db.assessment_template_question import AssessmentTemplateQuestion
from models.db.candidate_assessment import CandidateAssessment
from models.db.generated_assessment_question import GeneratedAssessmentQuestion
from models.db.job_requisition import JobRequisition
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


def _get_jr_id_for_assessment(assessment: "CandidateAssessment", db) -> Optional[int]:
    """Walk CandidateAssessment → Application → JobPosting → requisition_id."""
    try:
        app = db.query(Application).filter(
            Application.application_id == assessment.application_id
        ).first()
        if not app:
            return None
        posting = db.query(JobPosting).filter(
            JobPosting.posting_id == app.posting_id
        ).first()
        return posting.requisition_id if posting else None
    except Exception:
        return None


def _load_options_for_template_question(template_question_id: Optional[int], db) -> Optional[list]:
    """Fetch parsed options from AssessmentTemplateQuestion for a given question_id."""
    if not template_question_id:
        return None
    try:
        tq = db.query(AssessmentTemplateQuestion).filter(
            AssessmentTemplateQuestion.question_id == template_question_id
        ).first()
        if tq and tq.options:
            return json.loads(tq.options)
    except Exception:
        pass
    return None


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

        # Fetch time limit from config for the frontend countdown timer
        time_limit = None
        try:
            cfg = (
                db.query(TechnicalAssessmentConfig)
                .filter(TechnicalAssessmentConfig.config_id == assessment.config_id)
                .first()
            )
            if cfg:
                time_limit = cfg.time_limit_minutes
        except Exception:
            pass

        # ── Determine which question source to use ────────────────────────────
        # If the JR used the knowledge-based generator, serve from
        # generated_assessment_questions ordered by assessment_question_sets.position
        # and return template_question_id as question_id so grading works unchanged.
        jr_id = _get_jr_id_for_assessment(assessment, db)
        use_knowledge_questions = False
        if jr_id is not None:
            jr: Optional[JobRequisition] = (
                db.query(JobRequisition)
                .filter(JobRequisition.requisition_id == jr_id)
                .first()
            )
            use_knowledge_questions = bool(jr and jr.assessment_generated)

        if use_knowledge_questions:
            ordered_sets = (
                db.query(AssessmentQuestionSet)
                .filter(AssessmentQuestionSet.jr_id == jr_id)
                .order_by(AssessmentQuestionSet.position)
                .all()
            )
            gq_ids = [qs.question_id for qs in ordered_sets]
            gq_map: dict[int, GeneratedAssessmentQuestion] = {
                gq.id: gq
                for gq in db.query(GeneratedAssessmentQuestion)
                .filter(GeneratedAssessmentQuestion.id.in_(gq_ids))
                .all()
            }
            questions_payload = []
            for qs in ordered_sets:
                gq = gq_map.get(qs.question_id)
                if not gq or not gq.is_active:
                    continue
                # Return template_question_id as question_id so the submit
                # endpoint and grading pipeline work without modification.
                questions_payload.append({
                    "question_id": gq.template_question_id,
                    "question_text": gq.question_text,
                    "question_type": gq.question_type,
                    "points": 10,
                    "options": _load_options_for_template_question(gq.template_question_id, db),
                    "concept_name": gq.concept_name,
                    "tool_name": gq.tool_name,
                })
        else:
            # Legacy path: serve directly from AssessmentTemplateQuestion
            tqs = (
                db.query(AssessmentTemplateQuestion)
                .filter(AssessmentTemplateQuestion.template_id == assessment.template_id)
                .all()
            )
            questions_payload = [
                {
                    "question_id": q.question_id,
                    "question_text": q.question_text,
                    "question_type": q.question_type,
                    "points": q.points,
                    "options": json.loads(q.options) if q.options else None,
                }
                for q in tqs
            ]

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
                "questions": questions_payload,
            },
        )
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/assessment/{assessment_id}/submit — AI Grader pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _grade_mcq_inline(candidate_answer: str, correct_answer: str, points: int) -> tuple[float, str]:
    """Exact letter-match for MCQ — no LLM, sub-millisecond."""
    def _letter(s: str) -> str:
        s = s.strip().upper()
        return s[0] if s and s[0] in "ABCD" else s

    cand = _letter(candidate_answer)
    correct = _letter(correct_answer or "")
    if cand and cand == correct:
        return float(points), "Correct answer."
    return 0.0, f"Incorrect. The correct answer is '{correct}'." if correct else "Incorrect."


@assessment_router.post("/{assessment_id}/submit", response_model=AssessmentSubmitResponse)
def submit_assessment(assessment_id: int, payload: AssessmentSubmitRequest):
    """
    Saves candidate answers and returns immediately (no LLM on the request thread).

    MCQ: graded now via exact letter-match; score_awarded is stored.
    Open-ended: saved as raw text with score_awarded=NULL; graded post-deadline
      by the relative grading pipeline triggered by scan_and_dispatch_assessment_ranking.

    The returned total_score reflects MCQ points only; passed=None until the
    post-deadline pipeline resolves all open-ended scores.
    """
    if not verify_assessment_token(assessment_id, payload.token):
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
            raise HTTPException(status_code=404, detail="Assessment has no questions configured.")

        _check_deadline(assessment, db)

        # Load all template questions
        tqs = (
            db.query(AssessmentTemplateQuestion)
            .filter(AssessmentTemplateQuestion.template_id == assessment.template_id)
            .all()
        )
        # Index submitted answers by question_id
        submitted_map: dict[int, str] = {
            a.question_id: a.candidate_answer for a in payload.answers
        }

        passing_score = float(assessment.passing_score) if assessment.passing_score else None
        mcq_total = 0.0
        answer_results: list[AnswerResult] = []

        for tq in tqs:
            qid = tq.question_id
            qtype = (tq.question_type or "mcq").lower()
            points = tq.points or 10
            candidate_answer = submitted_map.get(qid, "")

            # Delete any pre-existing answer row (idempotent re-submission guard)
            db.query(AssessmentAnswer).filter(
                AssessmentAnswer.assessment_id == assessment_id,
                AssessmentAnswer.question_id == qid,
            ).delete(synchronize_session=False)

            if qtype == "mcq":
                score, feedback = _grade_mcq_inline(
                    candidate_answer, tq.correct_answer or "", points
                )
                mcq_total += score
                db.add(AssessmentAnswer(
                    assessment_id=assessment_id,
                    question_id=qid,
                    candidate_answer=candidate_answer,
                    score_awarded=score,
                    ai_feedback=feedback,
                ))
                answer_results.append(AnswerResult(
                    question_id=qid,
                    question_text=tq.question_text,
                    question_type=qtype,
                    candidate_answer=candidate_answer,
                    score_awarded=score,
                    max_points=points,
                    ai_feedback=feedback,
                ))
            else:
                # Open-ended: store raw, defer grading
                db.add(AssessmentAnswer(
                    assessment_id=assessment_id,
                    question_id=qid,
                    candidate_answer=candidate_answer,
                    score_awarded=None,
                    ai_feedback="Pending — graded after assessment deadline.",
                ))
                answer_results.append(AnswerResult(
                    question_id=qid,
                    question_text=tq.question_text,
                    question_type=qtype,
                    candidate_answer=candidate_answer,
                    score_awarded=None,
                    max_points=points,
                    ai_feedback="Your answer has been saved and will be graded after the deadline.",
                ))

        assessment.status = "Submitted"
        assessment.submitted_at = datetime.utcnow()
        db.commit()

        has_open_ended = any(r.question_type != "mcq" for r in answer_results)
        grading_note = (
            "MCQ answers have been graded. Open-ended answers will be graded after the "
            "assessment deadline and included in the final ranking."
            if has_open_ended else None
        )

        logger.info(
            "[submit_assessment] Assessment %d saved — MCQ score %.1f, "
            "%d open-ended deferred.",
            assessment_id,
            mcq_total,
            sum(1 for r in answer_results if r.question_type != "mcq"),
        )

        return AssessmentSubmitResponse(
            assessment_id=assessment_id,
            status="Submitted",
            total_score=mcq_total,
            passing_score=passing_score,
            passed=None,
            answers=answer_results,
            report=None,
            grading_note=grading_note,
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("[submit_assessment] Unexpected error for assessment %d.", assessment_id)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()

"""
Node 3 — tech_interview_node

Runs the CodeBERT + RoBERTa-QA + DeBERTa NLI ensemble on every completed
technical interview transcript that has not yet been scored.

Results are persisted to TechnicalInterviewSession so that score_candidates_node
can read the ensemble columns and prefer them over the raw overall_score.

Position in graph: after compute_weights, before hr_analysis.

Idempotent: sessions that already have codebert_score populated are skipped.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)


def tech_interview_node(state: FinalRankingState) -> FinalRankingState:
    """Score technical interview transcripts with the ensemble model and persist results."""
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    applications   = state.get("applications", [])
    job_title      = state.get("job_title") or "the role"

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        job_responsibilities = (
            (jr.key_responsibilities or jr.full_job_description or job_title)
            if jr else job_title
        )

        analyzed_count = 0
        skipped_count  = 0

        for app in applications:
            app_id    = app["application_id"]
            candidate = app.get("candidate")
            candidate_name = (
                f"{getattr(candidate, 'first_name', '') or ''} "
                f"{getattr(candidate, 'last_name', '') or ''}".strip()
                or "Candidate"
            )

            session: TechnicalInterviewSession = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == app_id,
                    TechnicalInterviewSession.status == "Completed",
                )
                .first()
            )
            if not session:
                continue

            # Idempotent — skip if ensemble scores already computed
            if session.codebert_score is not None:
                skipped_count += 1
                continue

            transcript = session.transcript or ""
            if not transcript.strip():
                logger.debug(
                    "[tech_interview_node] app_id=%d: no transcript — skipping.", app_id
                )
                continue

            try:
                from services.tech_interview_service import score_tech_transcript
                result = score_tech_transcript(
                    transcript=transcript,
                    job_title=job_title,
                    job_responsibilities=job_responsibilities,
                    candidate_name=candidate_name,
                )
                session.codebert_score        = result["codebert_score"]
                session.roberta_depth_score   = result["roberta_depth_score"]
                session.nli_technical_score   = result["nli_technical_score"]
                session.tfidf_technical_score = result["tfidf_technical_score"]
                session.shap_json             = result["shap_json"]
                session.shap_summary          = result["shap_summary"]
                analyzed_count += 1
                logger.info(
                    "[tech_interview_node] app_id=%d scored — composite=%.3f "
                    "(codebert=%.3f roberta=%.3f nli=%.3f tfidf=%.3f)",
                    app_id,
                    result["composite_score"],
                    result["codebert_score"],
                    result["roberta_depth_score"],
                    result["nli_technical_score"],
                    result["tfidf_technical_score"],
                )
            except Exception as exc:
                logger.warning(
                    "[tech_interview_node] app_id=%d scoring failed: %s", app_id, exc
                )

        if analyzed_count > 0:
            db.commit()

        logger.info(
            "[tech_interview_node] JR %d — %d transcripts scored, %d already had scores.",
            requisition_id, analyzed_count, skipped_count,
        )
        return {
            **state,
            "has_tech_analysis": (analyzed_count + skipped_count) > 0,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("[tech_interview_node] Unexpected error for JR %d.", requisition_id)
        return {**state, "error": str(exc)}
    finally:
        db.close()

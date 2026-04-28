"""
Node 2b — hr_analysis_node

Runs the ensemble HR soft-skills scoring engine (Go-Emotions + RoBERTa
Sentiment + DeBERTa NLI + BGE) on every HR interview transcript that has
not yet been scored.  Results are persisted directly to HRInterviewSession
so that score_candidates_node can read the ensemble columns.

Position in graph: after compute_weights, before score_candidates.

Idempotent: sessions that already have emotion_score populated are skipped.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_requisition import JobRequisition

logger = logging.getLogger(__name__)


def hr_analysis_node(state: FinalRankingState) -> FinalRankingState:
    """Score HR interview transcripts with the ensemble model and persist results."""
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

            session: HRInterviewSession = (
                db.query(HRInterviewSession)
                .filter(
                    HRInterviewSession.application_id == app_id,
                    HRInterviewSession.status == "Completed",
                )
                .first()
            )
            if not session:
                continue

            # Idempotent — skip if ensemble scores already computed
            if session.emotion_score is not None:
                skipped_count += 1
                continue

            transcript = session.transcript or ""
            if not transcript.strip():
                logger.debug(
                    "[hr_analysis_node] app_id=%d: no transcript — skipping.", app_id
                )
                continue

            try:
                from services.hr_analysis_service import score_transcript
                result = score_transcript(
                    transcript=transcript,
                    job_title=job_title,
                    job_responsibilities=job_responsibilities,
                    candidate_name=candidate_name,
                )
                session.emotion_score        = result["emotion_score"]
                session.sentiment_score      = result["sentiment_score"]
                session.nli_align_score      = result["nli_align_score"]
                session.semantic_depth_score = result["semantic_depth_score"]
                session.shap_json            = result["shap_json"]
                session.shap_summary         = result["shap_summary"]
                analyzed_count += 1
                logger.info(
                    "[hr_analysis_node] app_id=%d scored — composite=%.3f "
                    "(emotion=%.3f sentiment=%.3f nli=%.3f depth=%.3f)",
                    app_id,
                    result["composite_score"],
                    result["emotion_score"],
                    result["sentiment_score"],
                    result["nli_align_score"],
                    result["semantic_depth_score"],
                )
            except Exception as exc:
                logger.warning(
                    "[hr_analysis_node] app_id=%d scoring failed: %s", app_id, exc
                )

        if analyzed_count > 0:
            db.commit()

        logger.info(
            "[hr_analysis_node] JR %d — %d transcripts scored, %d already had scores.",
            requisition_id, analyzed_count, skipped_count,
        )
        return {
            **state,
            "has_hr_analysis": (analyzed_count + skipped_count) > 0,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("[hr_analysis_node] Unexpected error for JR %d.", requisition_id)
        return {**state, "error": str(exc)}
    finally:
        db.close()

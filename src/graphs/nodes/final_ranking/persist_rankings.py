"""
Node 5 — persist_rankings_node

Upserts FinalRanking rows for every scored candidate and transitions the
JobRequisition status to 'ranking_complete' via PipelineController.

Idempotent: if a FinalRanking row already exists for an application, it is
updated in-place (scores, rank, recommendation, red-flag).  Running this
node twice produces identical results.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.final_ranking import FinalRanking

logger = logging.getLogger(__name__)


def persist_rankings_node(state: FinalRankingState) -> FinalRankingState:
    """Upsert FinalRanking rows and advance JR status to ranking_complete."""
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    scored = state.get("scored", [])

    db = SessionLocal()
    try:
        for row in scored:
            existing: FinalRanking = (
                db.query(FinalRanking)
                .filter(FinalRanking.application_id == row["application_id"])
                .first()
            )
            if existing:
                existing.semantic_score            = row["semantic_score"]
                existing.assessment_score          = row["assessment_score"]
                existing.technical_interview_score = row["technical_interview_score"]
                existing.hr_interview_score        = row["hr_interview_score"]
                existing.weighted_total_score      = row["weighted_total_score"]
                existing.final_rank                = row["final_rank"]
                existing.final_recommendation      = row["final_recommendation"]
                existing.final_status              = row["final_status"]
                existing.red_flag                  = row["red_flag"]
                existing.red_flag_reason           = row["red_flag_reason"]
                existing.risk_score                = row.get("risk_score")
                existing.shap_json                 = row.get("shap_json")
                existing.shap_summary              = row.get("shap_summary")
            else:
                db.add(FinalRanking(
                    application_id=row["application_id"],
                    posting_id=row["posting_id"],
                    semantic_score=row["semantic_score"],
                    assessment_score=row["assessment_score"],
                    technical_interview_score=row["technical_interview_score"],
                    hr_interview_score=row["hr_interview_score"],
                    weighted_total_score=row["weighted_total_score"],
                    final_rank=row["final_rank"],
                    final_recommendation=row["final_recommendation"],
                    final_status=row["final_status"],
                    red_flag=row["red_flag"],
                    red_flag_reason=row["red_flag_reason"],
                    risk_score=row.get("risk_score"),
                    shap_json=row.get("shap_json"),
                    shap_summary=row.get("shap_summary"),
                ))

        from models.db.job_requisition import JobRequisition

        jr = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .with_for_update()
            .first()
        )
        if jr and jr.status != "ranking_complete":
            jr.status = "ranking_complete"

        db.commit()
        logger.info(
            "[final_ranking:persist] JR %d — %d FinalRanking rows upserted, "
            "status → 'ranking_complete'.",
            requisition_id,
            len(scored),
        )
        return {**state, "rankings_persisted": True}

    except Exception as exc:
        db.rollback()
        logger.exception(
            "[final_ranking:persist] DB error for JR %d.", requisition_id
        )
        return {**state, "error": str(exc)}
    finally:
        db.close()

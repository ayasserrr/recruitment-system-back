"""
Node 2 — compute_weights_node

Determines which score components are available in the pool and redistributes
weights proportionally when a component is missing.

Default weights:  CV 60% | Assessment 25% | Interview 15%
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_WEIGHT_CV = 0.60
_WEIGHT_ASSESSMENT = 0.25
_WEIGHT_INTERVIEW = 0.15


def compute_weights_node(state: FinalRankingState) -> FinalRankingState:
    """Probe the pool for available score components and normalise weights."""
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    posting_id = state.get("posting_id")

    db = SessionLocal()
    try:
        has_assessment = (
            db.query(AssessmentLeaderboard)
            .filter(AssessmentLeaderboard.jr_id == requisition_id)
            .first()
        ) is not None

        has_interview = (
            db.query(TechnicalInterviewSession)
            .join(
                Application,
                TechnicalInterviewSession.application_id == Application.application_id,
            )
            .filter(
                Application.posting_id == posting_id,
                TechnicalInterviewSession.status == "Completed",
                TechnicalInterviewSession.overall_score.isnot(None),
            )
            .first()
        ) is not None

        w_cv = _WEIGHT_CV
        w_assessment = _WEIGHT_ASSESSMENT if has_assessment else 0.0
        w_interview = _WEIGHT_INTERVIEW if has_interview else 0.0
        total_w = w_cv + w_assessment + w_interview
        if total_w == 0.0:
            total_w = 1.0  # safeguard against misconfiguration — should never happen

        weights = {
            "cv": round(w_cv / total_w, 4),
            "assessment": round(w_assessment / total_w, 4),
            "interview": round(w_interview / total_w, 4),
        }

        logger.info(
            "[final_ranking:weights] JR %d — CV=%.2f  Assessment=%.2f  Interview=%.2f "
            "(has_assessment=%s has_interview=%s)",
            requisition_id,
            weights["cv"], weights["assessment"], weights["interview"],
            has_assessment, has_interview,
        )
        return {
            **state,
            "has_assessment": has_assessment,
            "has_interview": has_interview,
            "weights": weights,
        }

    except Exception as exc:
        logger.exception("[final_ranking:weights] Error for JR %d.", requisition_id)
        return {**state, "error": str(exc)}
    finally:
        db.close()

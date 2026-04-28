"""
Node 2 — compute_weights_node

Determines which of the 4 score components are available in the pool and
redistributes weights proportionally when a component is missing.

Default weights:
    Screening (CV Match)       20%
    Technical Assessment       25%
    Technical Interview        30%
    HR Interview (Soft Skills) 25%
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.hr_interview_session import HRInterviewSession
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# Default weights — redistribute proportionally when a component is absent.
_WEIGHT_CV             = 0.20
_WEIGHT_ASSESSMENT     = 0.25
_WEIGHT_TECH_INTERVIEW = 0.30
_WEIGHT_HR_INTERVIEW   = 0.25


def compute_weights_node(state: FinalRankingState) -> FinalRankingState:
    """
    Probe the pool for available score components and normalise weights.

    has_tech_interview = True when ≥1 Completed TechnicalInterviewSession exists
                         with a non-null overall_score OR transcript.
    has_hr_interview   = True when ≥1 Completed HRInterviewSession exists
                         with a non-null overall_score OR transcript.
    """
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    posting_id     = state.get("posting_id")

    db = SessionLocal()
    try:
        has_assessment = (
            db.query(AssessmentLeaderboard)
            .filter(AssessmentLeaderboard.jr_id == requisition_id)
            .first()
        ) is not None

        has_tech_interview = (
            db.query(TechnicalInterviewSession)
            .join(
                Application,
                TechnicalInterviewSession.application_id == Application.application_id,
            )
            .filter(
                Application.posting_id == posting_id,
                TechnicalInterviewSession.status == "Completed",
            )
            .filter(
                (TechnicalInterviewSession.overall_score.isnot(None)) |
                (TechnicalInterviewSession.transcript.isnot(None))
            )
            .first()
        ) is not None

        has_hr_interview = (
            db.query(HRInterviewSession)
            .join(
                Application,
                HRInterviewSession.application_id == Application.application_id,
            )
            .filter(
                Application.posting_id == posting_id,
                HRInterviewSession.status == "Completed",
            )
            .filter(
                (HRInterviewSession.overall_score.isnot(None)) |
                (HRInterviewSession.transcript.isnot(None))
            )
            .first()
        ) is not None

        w_cv   = _WEIGHT_CV
        w_a    = _WEIGHT_ASSESSMENT     if has_assessment     else 0.0
        w_tech = _WEIGHT_TECH_INTERVIEW if has_tech_interview else 0.0
        w_hr   = _WEIGHT_HR_INTERVIEW   if has_hr_interview   else 0.0

        total_w = w_cv + w_a + w_tech + w_hr
        if total_w == 0.0:
            total_w = 1.0

        weights = {
            "cv":             round(w_cv   / total_w, 4),
            "assessment":     round(w_a    / total_w, 4),
            "tech_interview": round(w_tech / total_w, 4),
            "hr_interview":   round(w_hr   / total_w, 4),
        }

        logger.info(
            "[final_ranking:weights] JR %d — "
            "Screening=%.2f  Assessment=%.2f  TechInterview=%.2f  HR=%.2f "
            "(has_assessment=%s has_tech=%s has_hr=%s)",
            requisition_id,
            weights["cv"], weights["assessment"],
            weights["tech_interview"], weights["hr_interview"],
            has_assessment, has_tech_interview, has_hr_interview,
        )

        return {
            **state,
            "has_assessment":    has_assessment,
            "has_tech_interview": has_tech_interview,
            "has_hr_interview":  has_hr_interview,
            "weights":           weights,
        }

    except Exception as exc:
        logger.exception("[final_ranking:weights] Error for JR %d.", requisition_id)
        return {**state, "error": str(exc)}
    finally:
        db.close()

"""
Node 3 — score_candidates_node

For each application:
  • Fetches CV semantic score from SemanticAnalysisReport
  • Fetches assessment score from AssessmentLeaderboard (0-1 → ×100)
  • Fetches interview score from TechnicalInterviewSession.overall_score
  • Computes weighted_total_score using the weights from compute_weights_node
  • Applies the red-flag rule:
      cv_score >= RED_FLAG_CV_MIN AND interview_score < RED_FLAG_INTERVIEW_MAX
      → red_flag = True + red_flag_reason

Red-flag thresholds are intentionally conservative:
  Candidates who rank highly on CV but collapse in the live interview are a
  serious hire risk and need manual review before a decision is made.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_RED_FLAG_CV_MIN = 80.0
_RED_FLAG_INTERVIEW_MAX = 40.0


def score_candidates_node(state: FinalRankingState) -> FinalRankingState:
    """Compute weighted total score and detect red flags for every application."""
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    applications = state.get("applications", [])
    weights = state.get("weights", {"cv": 1.0, "assessment": 0.0, "interview": 0.0})
    has_assessment = state.get("has_assessment", False)
    has_interview = state.get("has_interview", False)

    db = SessionLocal()
    try:
        scored: list[dict] = []

        for app in applications:
            app_id = app["application_id"]
            posting_id = app["posting_id"]

            sem: SemanticAnalysisReport = (
                db.query(SemanticAnalysisReport)
                .filter(SemanticAnalysisReport.application_id == app_id)
                .first()
            )
            cv_score = float(sem.match_percentage) if sem else 0.0

            lb: AssessmentLeaderboard = (
                db.query(AssessmentLeaderboard)
                .filter(
                    AssessmentLeaderboard.application_id == app_id,
                    AssessmentLeaderboard.jr_id == requisition_id,
                )
                .first()
            )
            assessment_score = (
                float(lb.final_score) * 100.0
                if lb and lb.final_score is not None
                else None
            )

            session: TechnicalInterviewSession = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == app_id,
                    TechnicalInterviewSession.status == "Completed",
                )
                .first()
            )
            interview_score = (
                float(session.overall_score)
                if session and session.overall_score is not None
                else None
            )

            wtotal = weights["cv"] * cv_score
            if has_assessment:
                wtotal += weights["assessment"] * (assessment_score or 0.0)
            if has_interview:
                wtotal += weights["interview"] * (interview_score or 0.0)

            red_flag = False
            red_flag_reason = None
            if (
                cv_score >= _RED_FLAG_CV_MIN
                and interview_score is not None
                and interview_score < _RED_FLAG_INTERVIEW_MAX
            ):
                red_flag = True
                red_flag_reason = (
                    f"CV score {cv_score:.1f}% ranks candidate highly, but live interview "
                    f"score {interview_score:.1f}% is below threshold "
                    f"({_RED_FLAG_INTERVIEW_MAX}%). Manual review required."
                )
                logger.warning(
                    "[final_ranking:score] RED FLAG app_id=%d JR=%d CV=%.1f%% interview=%.1f%%",
                    app_id, requisition_id, cv_score, interview_score,
                )

            scored.append({
                "application_id": app_id,
                "posting_id": posting_id,
                "semantic_score": round(cv_score, 2),
                "assessment_score": round(assessment_score, 2) if assessment_score is not None else None,
                "technical_interview_score": round(interview_score, 2) if interview_score is not None else None,
                "hr_interview_score": None,
                "weighted_total_score": round(wtotal, 2),
                "red_flag": red_flag,
                "red_flag_reason": red_flag_reason,
                "candidate": app["candidate"],
            })

        logger.info(
            "[final_ranking:score] JR %d — %d candidates scored, %d red flags.",
            requisition_id,
            len(scored),
            sum(1 for s in scored if s["red_flag"]),
        )
        return {**state, "scored": scored}

    except Exception as exc:
        logger.exception("[final_ranking:score] Error scoring candidates for JR %d.", requisition_id)
        return {**state, "error": str(exc)}
    finally:
        db.close()

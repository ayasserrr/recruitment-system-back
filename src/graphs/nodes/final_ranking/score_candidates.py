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
from models.db.hr_interview_session import HRInterviewSession
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_RED_FLAG_CV_MIN = 80.0
_RED_FLAG_INTERVIEW_MAX = 40.0


def _to_100(score: float) -> float:
    """Normalize a score to the 0-100 range.
    Scores <= 10 are assumed to be on a 0-10 scale and are multiplied by 10.
    Scores > 10 are assumed to already be on 0-100 scale.
    """
    if score <= 10.0:
        return score * 10.0
    return min(score, 100.0)


def score_candidates_node(state: FinalRankingState) -> FinalRankingState:
    """Compute weighted total score and detect red flags for every application.

    Score sources (all normalized to 0-100):
      semantic_score            — SemanticAnalysisReport.match_percentage
      assessment_score          — AssessmentLeaderboard.final_score × 100
      technical_interview_score — TechnicalInterviewSession.overall_score (normalized)
      hr_interview_score        — HRInterviewSession.overall_score (normalized)
      interview_score (blended) — average of tech + HR when both present
    """
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

            # ── Semantic CV score (0-100) ──────────────────────────────────────
            sem: SemanticAnalysisReport = (
                db.query(SemanticAnalysisReport)
                .filter(SemanticAnalysisReport.application_id == app_id)
                .first()
            )
            cv_score = float(sem.match_percentage) if sem else 0.0

            # ── Assessment leaderboard score (0-100) ──────────────────────────
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

            # ── Technical interview score (normalized to 0-100) ───────────────
            tech_session: TechnicalInterviewSession = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == app_id,
                    TechnicalInterviewSession.status == "Completed",
                )
                .first()
            )
            tech_score_raw = (
                float(tech_session.overall_score)
                if tech_session and tech_session.overall_score is not None
                else None
            )
            tech_score = _to_100(tech_score_raw) if tech_score_raw is not None else None

            # ── HR interview score (normalized to 0-100) ──────────────────────
            hr_session: HRInterviewSession = (
                db.query(HRInterviewSession)
                .filter(
                    HRInterviewSession.application_id == app_id,
                    HRInterviewSession.status == "Completed",
                )
                .first()
            )
            hr_score_raw = (
                float(hr_session.overall_score)
                if hr_session and hr_session.overall_score is not None
                else None
            )
            hr_score = _to_100(hr_score_raw) if hr_score_raw is not None else None

            # ── Blended interview score (average of available components) ─────
            interview_components = [s for s in (tech_score, hr_score) if s is not None]
            interview_score = (
                sum(interview_components) / len(interview_components)
                if interview_components else None
            )

            # ── Weighted total ─────────────────────────────────────────────────
            wtotal = weights["cv"] * cv_score
            if has_assessment:
                wtotal += weights["assessment"] * (assessment_score or 0.0)
            if has_interview:
                wtotal += weights["interview"] * (interview_score or 0.0)

            # ── Red-flag detection ─────────────────────────────────────────────
            red_flag = False
            red_flag_reason = None
            if (
                cv_score >= _RED_FLAG_CV_MIN
                and interview_score is not None
                and interview_score < _RED_FLAG_INTERVIEW_MAX
            ):
                red_flag = True
                red_flag_reason = (
                    f"CV score {cv_score:.1f}% ranks candidate highly, but combined "
                    f"interview score {interview_score:.1f}% is below threshold "
                    f"({_RED_FLAG_INTERVIEW_MAX}%). Manual review required."
                )
                logger.warning(
                    "[final_ranking:score] RED FLAG app_id=%d JR=%d "
                    "CV=%.1f%% interview=%.1f%% (tech=%s hr=%s)",
                    app_id, requisition_id, cv_score, interview_score,
                    f"{tech_score:.1f}%" if tech_score is not None else "—",
                    f"{hr_score:.1f}%" if hr_score is not None else "—",
                )

            scored.append({
                "application_id": app_id,
                "posting_id": posting_id,
                "semantic_score": round(cv_score, 2),
                "assessment_score": round(assessment_score, 2) if assessment_score is not None else None,
                "technical_interview_score": round(tech_score, 2) if tech_score is not None else None,
                "hr_interview_score": round(hr_score, 2) if hr_score is not None else None,
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

"""
Node 5 — score_candidates_node

4-Stage Final Ranking scorer.

For each application:
  1. Screening score    — SemanticAnalysisReport.match_percentage      (0-100)
  2. Assessment score   — AssessmentLeaderboard.final_score × 100      (0-100)
  3. Tech Interview     — ensemble composite from TechnicalInterviewSession
                          (prefers codebert/roberta/nli/tfidf columns if populated
                           by tech_interview_node, else falls back to overall_score)
  4. HR Interview       — ensemble composite from HRInterviewSession
                          (prefers emotion/sentiment/nli/depth columns if populated
                           by hr_analysis_node, else falls back to overall_score)

Weighted total (default 20 / 25 / 30 / 25, proportionally redistributed
when any stage is absent):
    weighted_total = w_cv × screening
                   + w_a  × assessment
                   + w_ti × tech_interview
                   + w_hr × hr_interview

Cross-phase SHAP (analytical):
    φ_i = w_i × (score_i / 100 − 0.50)   for each stage i
    Positive φ → stage score above the average-candidate baseline.
    Negative φ → stage score below baseline.

Red-flag rules:
  RF-1 (Interview collapse):
      screening ≥ 80  AND  tech_interview < 40
      → strong CV but collapsed in live technical depth check.

  RF-2 (Cheating suspicion):
      assessment − tech_interview > RED_FLAG_CHEAT_GAP (default 30 pts)
      → aced the online test but couldn't demonstrate equivalent depth in the
         live interview; warrants manual review for assisted assessment.
"""

import json
import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.hr_interview_session import HRInterviewSession
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# ── Red-flag thresholds ───────────────────────────────────────────────────────
_RED_FLAG_CV_MIN         = 80.0   # RF-1: screening score that triggers a "strong CV"
_RED_FLAG_TECH_MIN       = 40.0   # RF-1: tech interview below this → collapse
_RED_FLAG_CHEAT_GAP      = 30.0   # RF-2: assessment − tech_interview gap → cheating suspicion

# ── SHAP baseline: average candidate (0–1 normalized) ────────────────────────
_SHAP_BASELINES = {
    "cv":             0.50,
    "assessment":     0.50,
    "tech_interview": 0.50,
    "hr_interview":   0.50,
}

# ── Ensemble weights per phase (must match the respective service files) ──────
_TECH_ENSEMBLE_WEIGHTS = {
    "codebert_score":        0.40,
    "roberta_depth_score":   0.30,
    "nli_technical_score":   0.20,
    "tfidf_technical_score": 0.10,
}
_HR_ENSEMBLE_WEIGHTS = {
    "nli_align_score":      0.35,
    "emotion_score":        0.25,
    "semantic_depth_score": 0.20,
    "sentiment_score":      0.20,
}


def _to_100(score: float) -> float:
    """Normalize a raw score to 0-100. Scores ≤ 10 are assumed 0-10 scale."""
    return score * 10.0 if score <= 10.0 else min(score, 100.0)


def _cross_phase_summary(
    shap_vals: dict,
    candidate_name: str,
    assessment_score: float | None,
    tech_score: float | None,
) -> str:
    """
    Human-readable SHAP narrative for the decision-maker report.
    Includes a cheating-suspicion flag when the assessment-tech gap is large.
    """
    sorted_contribs = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    lines = [f"Cross-Phase SHAP — {candidate_name}:"]
    for phase, phi in sorted_contribs:
        sign      = f"+{phi:.3f}" if phi >= 0 else f"{phi:.3f}"
        direction = "above" if phi >= 0 else "below"
        lines.append(f"  {phase}: {sign} vs average candidate ({direction} baseline)")

    # Identify the most and least influential stages
    if len(sorted_contribs) >= 2:
        top = sorted_contribs[0]
        lines.append(
            f"  → '{top[0]}' was the most influential stage "
            f"({'strength' if top[1] >= 0 else 'weakness'})."
        )

    # Cheating-suspicion callout in the narrative
    if (
        assessment_score is not None
        and tech_score is not None
        and assessment_score - tech_score > _RED_FLAG_CHEAT_GAP
    ):
        lines.append(
            f"  ⚠ Large gap: Assessment {assessment_score:.1f}% vs "
            f"Technical Interview {tech_score:.1f}% — manual review recommended."
        )

    return "\n".join(lines)


def score_candidates_node(state: FinalRankingState) -> FinalRankingState:
    """Compute 4-component weighted total, cross-phase SHAP, and red flags."""
    if state.get("error"):
        return state

    requisition_id    = state["requisition_id"]
    applications      = state.get("applications", [])
    weights           = state.get("weights", {
        "cv": 1.0, "assessment": 0.0, "tech_interview": 0.0, "hr_interview": 0.0,
    })
    has_assessment    = state.get("has_assessment", False)
    has_tech_interview = state.get("has_tech_interview", False)
    has_hr_interview  = state.get("has_hr_interview", False)

    db = SessionLocal()
    try:
        scored: list[dict] = []

        for app in applications:
            app_id     = app["application_id"]
            posting_id = app["posting_id"]
            candidate  = app.get("candidate")
            candidate_name = (
                f"{getattr(candidate, 'first_name', '') or ''} "
                f"{getattr(candidate, 'last_name', '') or ''}".strip()
                or "Candidate"
            )

            # ── 1. Screening score (0-100) ─────────────────────────────────
            sem: SemanticAnalysisReport = (
                db.query(SemanticAnalysisReport)
                .filter(SemanticAnalysisReport.application_id == app_id)
                .first()
            )
            cv_score = float(sem.match_percentage) if sem else 0.0

            # ── 2. Assessment score (0-100) ───────────────────────────────
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
                if lb and lb.final_score is not None else None
            )

            # ── 3. Technical Interview score (0-100) ──────────────────────
            # Prefer ensemble composite (computed by tech_interview_node).
            # Fall back to overall_score if ensemble was not computed.
            tech_session: TechnicalInterviewSession = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == app_id,
                    TechnicalInterviewSession.status == "Completed",
                )
                .first()
            )
            if tech_session and tech_session.codebert_score is not None:
                tech_composite_01 = sum(
                    _TECH_ENSEMBLE_WEIGHTS[feat]
                    * float(getattr(tech_session, feat) or 0)
                    for feat in _TECH_ENSEMBLE_WEIGHTS
                )
                tech_score = round(tech_composite_01 * 100.0, 2)
            elif tech_session and tech_session.overall_score is not None:
                tech_score = _to_100(float(tech_session.overall_score))
            else:
                tech_score = None

            # ── 4. HR Interview score (0-100) ─────────────────────────────
            # Prefer ensemble composite (computed by hr_analysis_node).
            hr_session: HRInterviewSession = (
                db.query(HRInterviewSession)
                .filter(
                    HRInterviewSession.application_id == app_id,
                    HRInterviewSession.status == "Completed",
                )
                .first()
            )
            if hr_session and hr_session.emotion_score is not None:
                hr_composite_01 = sum(
                    _HR_ENSEMBLE_WEIGHTS[feat]
                    * float(getattr(hr_session, feat) or 0)
                    for feat in _HR_ENSEMBLE_WEIGHTS
                )
                hr_score = round(hr_composite_01 * 100.0, 2)
            elif hr_session and hr_session.overall_score is not None:
                hr_score = _to_100(float(hr_session.overall_score))
            else:
                hr_score = None

            # ── Weighted total ────────────────────────────────────────────
            wtotal = weights["cv"] * cv_score
            if has_assessment:
                wtotal += weights["assessment"] * (assessment_score or 0.0)
            if has_tech_interview:
                wtotal += weights["tech_interview"] * (tech_score or 0.0)
            if has_hr_interview:
                wtotal += weights["hr_interview"] * (hr_score or 0.0)

            # ── Cross-phase SHAP (analytical, linear model) ───────────────
            w_cv   = weights.get("cv",             0.0)
            w_a    = weights.get("assessment",     0.0) if has_assessment     else 0.0
            w_tech = weights.get("tech_interview", 0.0) if has_tech_interview else 0.0
            w_hr   = weights.get("hr_interview",   0.0) if has_hr_interview   else 0.0

            phi_cv   = round(w_cv   * (cv_score               / 100.0 - _SHAP_BASELINES["cv"]),             4)
            phi_a    = round(w_a    * ((assessment_score or 0) / 100.0 - _SHAP_BASELINES["assessment"]),     4)
            phi_tech = round(w_tech * ((tech_score or 0)       / 100.0 - _SHAP_BASELINES["tech_interview"]), 4)
            phi_hr   = round(w_hr   * ((hr_score or 0)         / 100.0 - _SHAP_BASELINES["hr_interview"]),   4)

            shap_vals = {
                "screening":       phi_cv,
                "assessment":      phi_a,
                "tech_interview":  phi_tech,
                "hr_interview":    phi_hr,
            }
            shap_json_str    = json.dumps(shap_vals)
            shap_summary_str = _cross_phase_summary(
                shap_vals, candidate_name, assessment_score, tech_score
            )

            # ── Red-flag detection ────────────────────────────────────────
            red_flag        = False
            red_flag_reason = None

            # RF-1: strong CV but collapsed in live technical interview
            if (
                cv_score >= _RED_FLAG_CV_MIN
                and tech_score is not None
                and tech_score < _RED_FLAG_TECH_MIN
            ):
                red_flag = True
                red_flag_reason = (
                    f"RF-1 Interview Collapse: CV score {cv_score:.1f}% is strong, "
                    f"but Technical Interview score {tech_score:.1f}% is below "
                    f"threshold ({_RED_FLAG_TECH_MIN}%). Manual review required."
                )
                logger.warning(
                    "[final_ranking:score] RF-1 app_id=%d JR=%d CV=%.1f%% tech=%.1f%%",
                    app_id, requisition_id, cv_score, tech_score,
                )

            # RF-2: assessment score significantly higher than tech interview
            # (may indicate cheating / assisted assessment)
            if (
                assessment_score is not None
                and tech_score is not None
                and assessment_score - tech_score > _RED_FLAG_CHEAT_GAP
            ):
                cheat_reason = (
                    f"RF-2 Cheating Suspicion: Assessment score {assessment_score:.1f}% "
                    f"exceeds Technical Interview score {tech_score:.1f}% by "
                    f"{assessment_score - tech_score:.1f} points "
                    f"(threshold: {_RED_FLAG_CHEAT_GAP}pts). Possible assisted assessment."
                )
                red_flag = True
                red_flag_reason = (
                    f"{red_flag_reason} | {cheat_reason}"
                    if red_flag_reason else cheat_reason
                )
                logger.warning(
                    "[final_ranking:score] RF-2 app_id=%d JR=%d "
                    "assessment=%.1f%% tech=%.1f%% gap=%.1f%%",
                    app_id, requisition_id,
                    assessment_score, tech_score,
                    assessment_score - tech_score,
                )

            scored.append({
                "application_id":            app_id,
                "posting_id":                posting_id,
                "semantic_score":            round(cv_score, 2),
                "assessment_score":          round(assessment_score, 2) if assessment_score is not None else None,
                "technical_interview_score": round(tech_score, 2) if tech_score is not None else None,
                "hr_interview_score":        round(hr_score, 2)   if hr_score   is not None else None,
                "weighted_total_score":      round(wtotal, 2),
                "shap_json":                 shap_json_str,
                "shap_summary":              shap_summary_str,
                "red_flag":                  red_flag,
                "red_flag_reason":           red_flag_reason,
                "candidate":                 candidate,
            })

        logger.info(
            "[final_ranking:score] JR %d — %d candidates scored, %d red flags "
            "(%d RF-1 collapse, %d RF-2 cheat).",
            requisition_id,
            len(scored),
            sum(1 for s in scored if s["red_flag"]),
            sum(1 for s in scored if s["red_flag_reason"] and "RF-1" in s["red_flag_reason"]),
            sum(1 for s in scored if s["red_flag_reason"] and "RF-2" in s["red_flag_reason"]),
        )
        return {**state, "scored": scored}

    except Exception as exc:
        logger.exception(
            "[final_ranking:score] Error scoring candidates for JR %d.", requisition_id
        )
        return {**state, "error": str(exc)}
    finally:
        db.close()

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
                          (prefers nli/depth/sentiment columns if populated
                           by hr_analysis_node, else falls back to overall_score)

Weighted total (default 20 / 25 / 30 / 25, role-adjusted by compute_weights_node,
proportionally redistributed when any stage is absent):
    weighted_total = w_cv × screening
                   + w_a  × assessment
                   + w_ti × tech_interview
                   + w_hr × hr_interview

Cross-phase SHAP (pool-median baseline, analytical):
    φ_i = w_i × (score_i / 100 − median_i / 100)
    Positive φ → stage score above the pool median for that stage.
    Negative φ → stage score below the pool median.

Probabilistic risk score (replaces hard red-flag thresholds):
    RF-1 (Interview collapse):
        risk contribution when cv ≥ 80 AND tech < 40, using product of two sigmoids.
    RF-2 (Cheating suspicion):
        risk contribution when (assessment − tech) > 30, using a single sigmoid.
    risk_score = min(1.0, rf1 + rf2)   ∈ [0, 1]
    red_flag = risk_score ≥ 0.65
    Soft penalty applied to all candidates: score *= (1 − 0.15 × risk_score)
"""

import json
import logging
import math
import statistics

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.hr_interview_session import HRInterviewSession
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# ── Red-flag thresholds (used in sigmoid midpoints) ───────────────────────────
_RF1_CV_MIDPOINT   = 80.0   # sigmoid midpoint for "strong CV" axis
_RF1_TECH_MIDPOINT = 40.0   # sigmoid midpoint for "collapsed tech" axis (score below)
_RF2_GAP_MIDPOINT  = 30.0   # sigmoid midpoint for assessment-tech gap
_RISK_FLAG_THRESH  = 0.65   # risk_score ≥ this → red_flag = True
_RISK_SOFT_PENALTY = 0.15   # score *= (1 - _RISK_SOFT_PENALTY * risk_score)

# ── Ensemble weights per phase — MUST match services/tech_analysis_service.py
#    and services/hr_analysis_service.py exactly so the recomputed composite
#    here equals the overall_score stored on the session row.
_TECH_ENSEMBLE_WEIGHTS = {
    "nli_technical_score":   0.35,
    "codebert_score":        0.30,
    "tfidf_technical_score": 0.20,
    "roberta_depth_score":   0.15,
}
# emotion_score excluded from HR composite (diagnostic only — see hr_analysis_service.py)
_HR_ENSEMBLE_WEIGHTS = {
    "nli_align_score":      0.55,
    "semantic_depth_score": 0.35,
    "sentiment_score":      0.10,
}


def _to_100(score: float) -> float:
    """Normalize a raw score to 0-100. Scores ≤ 10 are assumed 0-10 scale."""
    return score * 10.0 if score <= 10.0 else min(score, 100.0)


def _sigmoid(x: float, midpoint: float = 0.0, steepness: float = 0.20) -> float:
    """Logistic sigmoid; returns values in (0, 1)."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def _compute_risk_score(
    cv_score: float,
    assessment_score: float | None,
    tech_score: float | None,
) -> float:
    """
    Continuous risk score in [0, 1].
    RF-1: strong CV but weak live technical (product of two sigmoids).
    RF-2: large assessment-tech gap suggesting assisted assessment (single sigmoid).
    """
    rf1 = 0.0
    if tech_score is not None:
        # Both conditions must be true: cv is high AND tech is low
        rf1 = _sigmoid(cv_score, _RF1_CV_MIDPOINT) * _sigmoid(_RF1_TECH_MIDPOINT - tech_score, 0.0)

    rf2 = 0.0
    if assessment_score is not None and tech_score is not None:
        rf2 = _sigmoid(assessment_score - tech_score, _RF2_GAP_MIDPOINT)

    return min(1.0, rf1 + rf2)


def _risk_reason(
    risk_score: float,
    cv_score: float,
    assessment_score: float | None,
    tech_score: float | None,
) -> str | None:
    if risk_score < 0.30:
        return None
    parts = []
    if tech_score is not None and cv_score >= 75 and tech_score < 45:
        parts.append(
            f"RF-1 Interview Collapse risk: CV {cv_score:.1f}% vs "
            f"Technical Interview {tech_score:.1f}% (risk={risk_score:.2f})"
        )
    if assessment_score is not None and tech_score is not None and assessment_score - tech_score > 20:
        parts.append(
            f"RF-2 Cheating Suspicion risk: Assessment {assessment_score:.1f}% vs "
            f"Technical Interview {tech_score:.1f}% gap={assessment_score - tech_score:.1f}pts "
            f"(risk={risk_score:.2f})"
        )
    return " | ".join(parts) if parts else f"Elevated risk score {risk_score:.2f} — manual review recommended."


def _pool_medians(raw_list: list[dict]) -> dict[str, float]:
    """
    Compute pool medians for each scoring stage.
    Uses 50% fallback when fewer than 2 non-null values exist for a stage.
    """
    def _median_or_half(vals: list[float]) -> float:
        valid = [v for v in vals if v is not None]
        return statistics.median(valid) if len(valid) >= 2 else 50.0

    return {
        "cv":             _median_or_half([r["cv_score"]           for r in raw_list]),
        "assessment":     _median_or_half([r["assessment_score"]   for r in raw_list if r["assessment_score"] is not None]),
        "tech_interview": _median_or_half([r["tech_score"]         for r in raw_list if r["tech_score"] is not None]),
        "hr_interview":   _median_or_half([r["hr_score"]           for r in raw_list if r["hr_score"] is not None]),
    }


def _cross_phase_summary(
    shap_vals: dict,
    candidate_name: str,
    risk_score: float,
    assessment_score: float | None,
    tech_score: float | None,
) -> str:
    sorted_contribs = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    lines = [f"Cross-Phase SHAP — {candidate_name}:"]
    for phase, phi in sorted_contribs:
        sign      = f"+{phi:.3f}" if phi >= 0 else f"{phi:.3f}"
        direction = "above" if phi >= 0 else "below"
        lines.append(f"  {phase}: {sign} vs pool median ({direction} median)")

    if len(sorted_contribs) >= 2:
        top = sorted_contribs[0]
        lines.append(
            f"  → '{top[0]}' was the most influential stage "
            f"({'strength' if top[1] >= 0 else 'weakness'})."
        )

    if risk_score >= 0.30:
        if (
            assessment_score is not None
            and tech_score is not None
            and assessment_score - tech_score > 20
        ):
            lines.append(
                f"  ⚠ Risk score {risk_score:.2f}: Assessment {assessment_score:.1f}% vs "
                f"Technical Interview {tech_score:.1f}% — manual review recommended."
            )
        elif tech_score is not None:
            lines.append(f"  ⚠ Elevated risk score {risk_score:.2f} — manual review recommended.")

    return "\n".join(lines)


def score_candidates_node(state: FinalRankingState) -> FinalRankingState:
    """Compute 4-component weighted total, cross-phase SHAP, and probabilistic risk scores."""
    if state.get("error"):
        return state

    requisition_id     = state["requisition_id"]
    applications       = state.get("applications", [])
    weights            = state.get("weights", {
        "cv": 1.0, "assessment": 0.0, "tech_interview": 0.0, "hr_interview": 0.0,
    })
    has_assessment     = state.get("has_assessment", False)
    has_tech_interview = state.get("has_tech_interview", False)
    has_hr_interview   = state.get("has_hr_interview", False)

    db = SessionLocal()
    try:
        # ── Phase 1: collect raw scores for all candidates ────────────────────
        raw: list[dict] = []

        for app in applications:
            app_id     = app["application_id"]
            posting_id = app["posting_id"]
            candidate  = app.get("candidate")

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
                if lb and lb.final_score is not None else None
            )

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

            hr_session: HRInterviewSession = (
                db.query(HRInterviewSession)
                .filter(
                    HRInterviewSession.application_id == app_id,
                    HRInterviewSession.status == "Completed",
                )
                .first()
            )
            # Use nli_align_score as sentinel (emotion excluded from composite)
            if hr_session and hr_session.nli_align_score is not None:
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

            raw.append({
                "application_id":  app_id,
                "posting_id":      posting_id,
                "candidate":       candidate,
                "cv_score":        cv_score,
                "assessment_score": assessment_score,
                "tech_score":      tech_score,
                "hr_score":        hr_score,
            })

        # ── Phase 2: pool-median baselines for SHAP ───────────────────────────
        pool_med = _pool_medians(raw)
        logger.info(
            "[final_ranking:score] JR %d pool medians — CV=%.1f Assessment=%.1f Tech=%.1f HR=%.1f",
            requisition_id,
            pool_med["cv"], pool_med["assessment"],
            pool_med["tech_interview"], pool_med["hr_interview"],
        )

        # ── Phase 3: score each candidate with medians and risk ───────────────
        scored: list[dict] = []

        for r in raw:
            app_id          = r["application_id"]
            posting_id      = r["posting_id"]
            candidate       = r["candidate"]
            cv_score        = r["cv_score"]
            assessment_score = r["assessment_score"]
            tech_score      = r["tech_score"]
            hr_score        = r["hr_score"]

            candidate_name = (
                f"{getattr(candidate, 'first_name', '') or ''} "
                f"{getattr(candidate, 'last_name', '') or ''}".strip()
                or "Candidate"
            )

            # ── Weighted total ────────────────────────────────────────────────
            wtotal = weights["cv"] * cv_score
            if has_assessment:
                wtotal += weights["assessment"] * (assessment_score or 0.0)
            if has_tech_interview:
                wtotal += weights["tech_interview"] * (tech_score or 0.0)
            if has_hr_interview:
                wtotal += weights["hr_interview"] * (hr_score or 0.0)

            # ── Probabilistic risk score ──────────────────────────────────────
            risk_score = _compute_risk_score(cv_score, assessment_score, tech_score)

            # Soft penalty applied before final score (always, proportional to risk)
            wtotal = round(wtotal * (1.0 - _RISK_SOFT_PENALTY * risk_score), 2)

            red_flag        = risk_score >= _RISK_FLAG_THRESH
            red_flag_reason = _risk_reason(risk_score, cv_score, assessment_score, tech_score)

            if red_flag:
                logger.warning(
                    "[final_ranking:score] risk=%.2f app_id=%d JR=%d CV=%.1f "
                    "tech=%s assessment=%s",
                    risk_score, app_id, requisition_id, cv_score,
                    f"{tech_score:.1f}" if tech_score is not None else "N/A",
                    f"{assessment_score:.1f}" if assessment_score is not None else "N/A",
                )

            # ── Cross-phase SHAP (pool-median baseline) ───────────────────────
            w_cv   = weights.get("cv",             0.0)
            w_a    = weights.get("assessment",     0.0) if has_assessment     else 0.0
            w_tech = weights.get("tech_interview", 0.0) if has_tech_interview else 0.0
            w_hr   = weights.get("hr_interview",   0.0) if has_hr_interview   else 0.0

            phi_cv   = round(w_cv   * (cv_score                / 100.0 - pool_med["cv"]             / 100.0), 4)
            phi_a    = round(w_a    * ((assessment_score or 0)  / 100.0 - pool_med["assessment"]     / 100.0), 4)
            phi_tech = round(w_tech * ((tech_score or 0)        / 100.0 - pool_med["tech_interview"] / 100.0), 4)
            phi_hr   = round(w_hr   * ((hr_score or 0)          / 100.0 - pool_med["hr_interview"]   / 100.0), 4)

            shap_vals = {
                "screening":      phi_cv,
                "assessment":     phi_a,
                "tech_interview": phi_tech,
                "hr_interview":   phi_hr,
            }
            shap_json_str    = json.dumps(shap_vals)
            shap_summary_str = _cross_phase_summary(
                shap_vals, candidate_name, risk_score, assessment_score, tech_score
            )

            scored.append({
                "application_id":            app_id,
                "posting_id":                posting_id,
                "semantic_score":            round(cv_score, 2),
                "assessment_score":          round(assessment_score, 2) if assessment_score is not None else None,
                "technical_interview_score": round(tech_score, 2) if tech_score is not None else None,
                "hr_interview_score":        round(hr_score, 2)   if hr_score   is not None else None,
                "weighted_total_score":      wtotal,
                "risk_score":                round(risk_score, 4),
                "shap_json":                 shap_json_str,
                "shap_summary":              shap_summary_str,
                "red_flag":                  red_flag,
                "red_flag_reason":           red_flag_reason,
                "candidate":                 candidate,
            })

        logger.info(
            "[final_ranking:score] JR %d — %d candidates scored, %d red flags.",
            requisition_id,
            len(scored),
            sum(1 for s in scored if s["red_flag"]),
        )
        return {**state, "scored": scored}

    except Exception as exc:
        logger.exception(
            "[final_ranking:score] Error scoring candidates for JR %d.", requisition_id
        )
        return {**state, "error": str(exc)}
    finally:
        db.close()

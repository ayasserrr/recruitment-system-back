"""
Node 2 — compute_weights_node

Determines which of the 4 score components are available in the pool and
redistributes weights proportionally when a component is missing.

Base weights are chosen based on the seniority level inferred from the job title:
    junior: CV 20%, Assessment 35%, Tech Interview 25%, HR 20%
      mid:  CV 20%, Assessment 25%, Tech Interview 30%, HR 25%
   senior:  CV 15%, Assessment 20%, Tech Interview 35%, HR 30%
    staff:  CV 10%, Assessment 15%, Tech Interview 40%, HR 35%

Rationale: junior roles are best distinguished by structured assessment;
staff roles are best distinguished by depth demonstrated in live interviews.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# ── Seniority keyword sets ────────────────────────────────────────────────────
_STAFF_KEYWORDS  = frozenset({"staff", "principal", "architect", "director", "vp", "head of", "chief"})
_SENIOR_KEYWORDS = frozenset({"senior", "sr.", "sr ", "lead", "tech lead"})
_JUNIOR_KEYWORDS = frozenset({"junior", "jr.", "jr ", "entry", "associate", "intern", "trainee", "graduate"})

# ── Role-aware base weight tables ─────────────────────────────────────────────
_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "junior": {"cv": 0.20, "assessment": 0.35, "tech_interview": 0.25, "hr_interview": 0.20},
    "mid":    {"cv": 0.20, "assessment": 0.25, "tech_interview": 0.30, "hr_interview": 0.25},
    "senior": {"cv": 0.15, "assessment": 0.20, "tech_interview": 0.35, "hr_interview": 0.30},
    "staff":  {"cv": 0.10, "assessment": 0.15, "tech_interview": 0.40, "hr_interview": 0.35},
}


def _detect_seniority(job_title: str) -> str:
    """Return 'junior', 'mid', 'senior', or 'staff' from the job title string."""
    title = (job_title or "").lower()
    if any(k in title for k in _STAFF_KEYWORDS):
        return "staff"
    if any(k in title for k in _SENIOR_KEYWORDS):
        return "senior"
    if any(k in title for k in _JUNIOR_KEYWORDS):
        return "junior"
    return "mid"


def compute_weights_node(state: FinalRankingState) -> FinalRankingState:
    """
    Probe the pool for available score components, detect role seniority, and
    normalise role-aware weights.

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
        # ── Detect seniority from job title ───────────────────────────────────
        job_title = state.get("job_title") or ""
        if not job_title:
            jr: JobRequisition = (
                db.query(JobRequisition)
                .filter(JobRequisition.requisition_id == requisition_id)
                .first()
            )
            job_title = (jr.job_title or "") if jr else ""
        seniority = _detect_seniority(job_title)
        base_w    = _ROLE_WEIGHTS[seniority]

        # ── Probe available stages ────────────────────────────────────────────
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

        w_cv   = base_w["cv"]
        w_a    = base_w["assessment"]     if has_assessment     else 0.0
        w_tech = base_w["tech_interview"] if has_tech_interview else 0.0
        w_hr   = base_w["hr_interview"]   if has_hr_interview   else 0.0

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
            "[final_ranking:weights] JR %d '%s' (seniority=%s) — "
            "Screening=%.2f  Assessment=%.2f  TechInterview=%.2f  HR=%.2f "
            "(has_assessment=%s has_tech=%s has_hr=%s)",
            requisition_id, job_title, seniority,
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

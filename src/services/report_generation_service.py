"""
Interview Report Generation Service
══════════════════════════════════════════════════════════════════════════════
Generates TechnicalInterviewReport and HRInterviewReport rows from the
pre-trained model scores already stored on the session rows.

Zero LLM dependency — all narrative is template-driven from scores and SHAP.

Public API
──────────
generate_technical_report(session_id)   → TechnicalInterviewReport
generate_hr_report(session_id)          → HRInterviewReport
update_technical_pool_ranks(requisition_id)  → updates rank_in_pool for all tech reports
update_hr_pool_ranks(requisition_id)         → updates rank_in_pool for all HR reports
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Score interpretation thresholds ──────────────────────────────────────────

def _tier(score_0_100: float) -> str:
    if score_0_100 >= 80:
        return "Excellent"
    if score_0_100 >= 65:
        return "Good"
    if score_0_100 >= 50:
        return "Satisfactory"
    return "Below Expectations"


def _recommendation(score_0_100: float) -> str:
    if score_0_100 >= 80:
        return "Strongly Recommended"
    if score_0_100 >= 65:
        return "Recommended"
    if score_0_100 >= 50:
        return "Consider with Reservations"
    return "Not Recommended"


def _pct(v: Optional[float]) -> str:
    """Format a 0-1 float as a percentage string."""
    if v is None:
        return "N/A"
    return f"{v * 100:.1f}%"


def _rating(v: Optional[float], good: float = 0.55, great: float = 0.70) -> str:
    if v is None:
        return "unavailable"
    if v >= great:
        return "strong"
    if v >= good:
        return "adequate"
    return "weak"


# ── Technical report ──────────────────────────────────────────────────────────

def generate_technical_report(session_id: int) -> bool:
    """
    Read TechnicalInterviewSession, build a TechnicalInterviewReport row.
    Idempotent — updates an existing row if one already exists.
    Returns True on success.
    """
    from database.connection import SessionLocal
    from models.db.technical_interview_session import TechnicalInterviewSession
    from models.db.technical_interview_report import TechnicalInterviewReport
    from models.db.application import Application
    from models.db.candidate import Candidate
    from models.db.job_posting import JobPosting
    from models.db.job_requisition import JobRequisition
    from sqlalchemy.orm import joinedload

    db = SessionLocal()
    try:
        session: Optional[TechnicalInterviewSession] = (
            db.query(TechnicalInterviewSession)
            .options(
                joinedload(TechnicalInterviewSession.application)
                .joinedload(Application.candidate),
                joinedload(TechnicalInterviewSession.application)
                .joinedload(Application.posting)
                .joinedload(JobPosting.requisition),
            )
            .filter(TechnicalInterviewSession.session_id == session_id)
            .first()
        )
        if not session:
            logger.warning("[report_gen] TechnicalInterviewSession %d not found.", session_id)
            return False

        application: Application = session.application
        candidate: Candidate    = application.candidate
        jr: Optional[JobRequisition] = (
            application.posting.requisition if application.posting else None
        )

        candidate_name = f"{candidate.first_name} {candidate.last_name}" if candidate else "Candidate"
        job_title      = (jr.job_title or "the role") if jr else "the role"
        score_100      = float(session.overall_score or 0)
        tier           = _tier(score_100)
        rec            = _recommendation(score_100)

        # ── Individual model scores ──────────────────────────────────────────
        nli    = float(session.nli_technical_score   or 0)
        cbert  = float(session.codebert_score        or 0)
        tfidf  = float(session.tfidf_technical_score or 0)
        roberta = float(session.roberta_depth_score  or 0)

        # ── SHAP ─────────────────────────────────────────────────────────────
        shap: dict = {}
        if session.shap_json:
            try:
                shap = json.loads(session.shap_json)
            except (json.JSONDecodeError, TypeError):
                pass

        top_positive = sorted(
            [(k, v) for k, v in shap.items() if v > 0.005],
            key=lambda x: x[1], reverse=True
        )[:2]
        top_negative = sorted(
            [(k, v) for k, v in shap.items() if v < -0.005],
            key=lambda x: x[1]
        )[:1]

        # ── Strengths ────────────────────────────────────────────────────────
        strengths: list[str] = []
        _TECH_LABELS = {
            "nli_technical_score":   "Role alignment (DeBERTa NLI)",
            "codebert_score":        "Technical depth (CodeBERT)",
            "tfidf_technical_score": "Keyword coverage (TF-IDF)",
            "roberta_depth_score":   "Response confidence (RoBERTa)",
        }
        if nli    >= 0.60: strengths.append(f"Strong role alignment — NLI score {_pct(nli)}")
        if cbert  >= 0.60: strengths.append(f"High technical semantic depth — CodeBERT {_pct(cbert)}")
        if tfidf  >= 0.45: strengths.append(f"Good technical vocabulary coverage — TF-IDF {_pct(tfidf)}")
        if roberta >= 0.60: strengths.append(f"Confident, professional delivery — RoBERTa {_pct(roberta)}")
        for feat, val in top_positive:
            label = _TECH_LABELS.get(feat, feat)
            if f"{label}" not in " ".join(strengths):
                strengths.append(f"{label} above baseline (+{val:.3f} SHAP)")
        if not strengths:
            strengths.append("Completed all interview questions")

        # ── Weaknesses ───────────────────────────────────────────────────────
        weaknesses: list[str] = []
        if nli    < 0.45: weaknesses.append(f"Limited demonstration of role-specific technical knowledge (NLI {_pct(nli)})")
        if cbert  < 0.40: weaknesses.append(f"Low technical semantic depth relative to job description (CodeBERT {_pct(cbert)})")
        if tfidf  < 0.25: weaknesses.append(f"Insufficient use of expected technical terminology (TF-IDF {_pct(tfidf)})")
        if roberta < 0.45: weaknesses.append(f"Low-confidence or negative-tone responses detected (RoBERTa {_pct(roberta)})")
        for feat, val in top_negative:
            label = _TECH_LABELS.get(feat, feat)
            weaknesses.append(f"{label} below baseline ({val:.3f} SHAP)")
        if not weaknesses:
            weaknesses.append("No critical weaknesses identified")

        # ── Interview summary (short) ─────────────────────────────────────────
        interview_summary = (
            f"{candidate_name} completed the technical interview for the {job_title} role. "
            f"Ensemble score: {score_100:.1f}/100 ({tier}). "
            f"Recommendation: {rec}."
        )

        # ── Detailed assessment ───────────────────────────────────────────────
        shap_lines = ""
        if top_positive:
            shap_lines += "  Positive drivers: " + "; ".join(
                f"{_TECH_LABELS.get(f, f)} +{v:.3f}" for f, v in top_positive
            ) + ".\n"
        if top_negative:
            shap_lines += "  Negative drivers: " + "; ".join(
                f"{_TECH_LABELS.get(f, f)} {v:.3f}" for f, v in top_negative
            ) + ".\n"

        detailed_assessment = (
            f"TECHNICAL INTERVIEW ASSESSMENT — {candidate_name}\n"
            f"{'='*60}\n\n"
            f"Role:            {job_title}\n"
            f"Score:           {score_100:.1f} / 100  ({tier})\n"
            f"Recommendation:  {rec}\n\n"
            f"MODEL SCORES\n"
            f"{'-'*40}\n"
            f"  Role Alignment   (DeBERTa NLI):    {_pct(nli):>8}  [{_rating(nli)}]\n"
            f"  Technical Depth  (CodeBERT):        {_pct(cbert):>8}  [{_rating(cbert)}]\n"
            f"  Keyword Coverage (TF-IDF):          {_pct(tfidf):>8}  [{_rating(tfidf, 0.30, 0.50)}]\n"
            f"  Confidence       (RoBERTa):         {_pct(roberta):>8}  [{_rating(roberta)}]\n\n"
            f"SCORE DRIVERS (SHAP)\n"
            f"{'-'*40}\n"
            f"{shap_lines}"
            f"\nSTRENGTHS\n"
            f"{chr(10).join('  • ' + s for s in strengths)}\n\n"
            f"AREAS FOR IMPROVEMENT\n"
            f"{chr(10).join('  • ' + w for w in weaknesses)}\n"
        )

        # ── Persist report ────────────────────────────────────────────────────
        existing_report = (
            db.query(TechnicalInterviewReport)
            .filter(TechnicalInterviewReport.session_id == session_id)
            .first()
        )
        if existing_report:
            report = existing_report
        else:
            report = TechnicalInterviewReport(session_id=session_id)
            db.add(report)

        report.interview_summary  = interview_summary
        report.detailed_assessment = detailed_assessment
        report.strengths          = json.dumps(strengths)
        report.weaknesses         = json.dumps(weaknesses)
        report.recommendation     = rec
        report.generated_at       = datetime.utcnow()

        db.commit()
        logger.info("[report_gen] TechnicalInterviewReport saved for session %d.", session_id)
        return True

    except Exception:
        db.rollback()
        logger.exception("[report_gen] Failed to generate technical report for session %d.", session_id)
        return False
    finally:
        db.close()


# ── HR report ─────────────────────────────────────────────────────────────────

def generate_hr_report(session_id: int) -> bool:
    """
    Read HRInterviewSession, build an HRInterviewReport row.
    Idempotent — updates an existing row if one already exists.
    Returns True on success.
    """
    from database.connection import SessionLocal
    from models.db.hr_interview_session import HRInterviewSession
    from models.db.hr_interview_report import HRInterviewReport
    from models.db.application import Application
    from models.db.candidate import Candidate
    from models.db.job_posting import JobPosting
    from models.db.job_requisition import JobRequisition
    from sqlalchemy.orm import joinedload

    db = SessionLocal()
    try:
        session: Optional[HRInterviewSession] = (
            db.query(HRInterviewSession)
            .options(
                joinedload(HRInterviewSession.application)
                .joinedload(Application.candidate),
                joinedload(HRInterviewSession.application)
                .joinedload(Application.posting)
                .joinedload(JobPosting.requisition),
            )
            .filter(HRInterviewSession.session_id == session_id)
            .first()
        )
        if not session:
            logger.warning("[report_gen] HRInterviewSession %d not found.", session_id)
            return False

        application: Application = session.application
        candidate: Candidate    = application.candidate
        jr: Optional[JobRequisition] = (
            application.posting.requisition if application.posting else None
        )

        candidate_name = f"{candidate.first_name} {candidate.last_name}" if candidate else "Candidate"
        job_title      = (jr.job_title or "the role") if jr else "the role"
        score_100      = float(session.overall_score or 0)
        tier           = _tier(score_100)
        rec            = _recommendation(score_100)

        # ── Individual model scores ──────────────────────────────────────────
        nli      = float(session.nli_align_score      or 0)
        emotion  = float(session.emotion_score        or 0)
        depth    = float(session.semantic_depth_score or 0)
        sentiment = float(session.sentiment_score     or 0)

        # ── SHAP ─────────────────────────────────────────────────────────────
        shap: dict = {}
        if session.shap_json:
            try:
                shap = json.loads(session.shap_json)
            except (json.JSONDecodeError, TypeError):
                pass

        top_positive = sorted(
            [(k, v) for k, v in shap.items() if v > 0.005],
            key=lambda x: x[1], reverse=True
        )[:2]
        top_negative = sorted(
            [(k, v) for k, v in shap.items() if v < -0.005],
            key=lambda x: x[1]
        )[:1]

        _HR_LABELS = {
            "nli_align_score":      "Role relevance (DeBERTa NLI)",
            "semantic_depth_score": "Semantic depth (BGE)",
            "sentiment_score":      "Professional tone (RoBERTa)",
        }

        # ── Strengths (composite features only — emotion excluded from scoring) ─
        strengths: list[str] = []
        if nli      >= 0.60: strengths.append(f"Answers consistently relevant to the role (NLI {_pct(nli)})")
        if depth    >= 0.55: strengths.append(f"Semantically rich responses aligned with JD (BGE {_pct(depth)})")
        if sentiment >= 0.60: strengths.append(f"Professional, confident tone throughout (RoBERTa {_pct(sentiment)})")
        for feat, val in top_positive:
            label = _HR_LABELS.get(feat, feat)
            if label not in " ".join(strengths):
                strengths.append(f"{label} above pool median (+{val:.3f} SHAP)")
        if not strengths:
            strengths.append("Completed all behavioral interview questions")

        # ── Weaknesses ───────────────────────────────────────────────────────
        weaknesses: list[str] = []
        if nli      < 0.45: weaknesses.append(f"Responses show limited alignment with role requirements (NLI {_pct(nli)})")
        if depth    < 0.35: weaknesses.append(f"Shallow semantic content relative to job description (BGE {_pct(depth)})")
        if sentiment < 0.45: weaknesses.append(f"Tone appears uncertain or negative in key responses (RoBERTa {_pct(sentiment)})")
        for feat, val in top_negative:
            label = _HR_LABELS.get(feat, feat)
            weaknesses.append(f"{label} below pool median ({val:.3f} SHAP)")
        if not weaknesses:
            weaknesses.append("No critical soft-skill weaknesses identified")

        # ── Interview summary ─────────────────────────────────────────────────
        interview_summary = (
            f"{candidate_name} completed the behavioral HR interview for the {job_title} role. "
            f"Composite score: {score_100:.1f}/100 ({tier}). "
            f"Recommendation: {rec}."
        )

        # ── Detailed assessment ───────────────────────────────────────────────
        shap_lines = ""
        if top_positive:
            shap_lines += "  Positive drivers: " + "; ".join(
                f"{_HR_LABELS.get(f, f)} +{v:.3f}" for f, v in top_positive
            ) + ".\n"
        if top_negative:
            shap_lines += "  Negative drivers: " + "; ".join(
                f"{_HR_LABELS.get(f, f)} {v:.3f}" for f, v in top_negative
            ) + ".\n"

        detailed_assessment = (
            f"HR BEHAVIORAL INTERVIEW ASSESSMENT — {candidate_name}\n"
            f"{'='*60}\n\n"
            f"Role:            {job_title}\n"
            f"Score:           {score_100:.1f} / 100  ({tier})\n"
            f"Recommendation:  {rec}\n\n"
            f"COMPOSITE SCORES  (weights: NLI 55% | Depth 35% | Tone 10%)\n"
            f"{'-'*40}\n"
            f"  Role Relevance   (DeBERTa NLI):    {_pct(nli):>8}  [{_rating(nli)}]\n"
            f"  Semantic Depth   (BGE):            {_pct(depth):>8}  [{_rating(depth)}]\n"
            f"  Professionalism  (RoBERTa):        {_pct(sentiment):>8}  [{_rating(sentiment)}]\n\n"
            f"DIAGNOSTIC SIGNAL  (not scored — for human review only)\n"
            f"{'-'*40}\n"
            f"  Emotional Tone   (Go-Emotions):    {_pct(emotion):>8}  [diagnostic]\n\n"
            f"SCORE DRIVERS (SHAP vs pool median)\n"
            f"{'-'*40}\n"
            f"{shap_lines}"
            f"\nSTRENGTHS\n"
            f"{chr(10).join('  • ' + s for s in strengths)}\n\n"
            f"AREAS FOR DEVELOPMENT\n"
            f"{chr(10).join('  • ' + w for w in weaknesses)}\n"
        )

        # ── Persist report ────────────────────────────────────────────────────
        existing = (
            db.query(HRInterviewReport)
            .filter(HRInterviewReport.session_id == session_id)
            .first()
        )
        if existing:
            report = existing
        else:
            report = HRInterviewReport(session_id=session_id)
            db.add(report)

        report.interview_summary   = interview_summary
        report.detailed_assessment = detailed_assessment
        report.strengths           = json.dumps(strengths)
        report.weaknesses          = json.dumps(weaknesses)
        report.recommendation      = rec
        report.generated_at        = datetime.utcnow()

        db.commit()
        logger.info("[report_gen] HRInterviewReport saved for session %d.", session_id)
        return True

    except Exception:
        db.rollback()
        logger.exception("[report_gen] Failed to generate HR report for session %d.", session_id)
        return False
    finally:
        db.close()


# ── Pool ranking ──────────────────────────────────────────────────────────────

def update_technical_pool_ranks(requisition_id: int) -> int:
    """
    Rank all Completed TechnicalInterviewSessions for a requisition by
    overall_score (desc) and write rank_in_pool to their reports.
    Returns the number of reports updated.
    """
    from database.connection import SessionLocal
    from models.db.technical_interview_session import TechnicalInterviewSession
    from models.db.technical_interview_report import TechnicalInterviewReport
    from models.db.application import Application
    from models.db.job_posting import JobPosting

    db = SessionLocal()
    try:
        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return 0

        sessions = (
            db.query(TechnicalInterviewSession)
            .join(Application, TechnicalInterviewSession.application_id == Application.application_id)
            .filter(
                Application.posting_id == posting.posting_id,
                TechnicalInterviewSession.status == "Completed",
            )
            .order_by(TechnicalInterviewSession.overall_score.desc().nullslast())
            .all()
        )

        updated = 0
        for rank, sess in enumerate(sessions, start=1):
            report = (
                db.query(TechnicalInterviewReport)
                .filter(TechnicalInterviewReport.session_id == sess.session_id)
                .first()
            )
            if report:
                report.rank_in_pool = rank
                updated += 1

        db.commit()
        logger.info(
            "[report_gen] Updated tech pool ranks for JR %d — %d reports.", requisition_id, updated
        )
        return updated

    except Exception:
        db.rollback()
        logger.exception("[report_gen] Failed to update tech pool ranks for JR %d.", requisition_id)
        return 0
    finally:
        db.close()


def update_hr_pool_ranks(requisition_id: int) -> int:
    """
    Rank all Completed HRInterviewSessions for a requisition by
    overall_score (desc) and write rank_in_pool to their reports.
    Returns the number of reports updated.
    """
    from database.connection import SessionLocal
    from models.db.hr_interview_session import HRInterviewSession
    from models.db.hr_interview_report import HRInterviewReport
    from models.db.application import Application
    from models.db.job_posting import JobPosting

    db = SessionLocal()
    try:
        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return 0

        sessions = (
            db.query(HRInterviewSession)
            .join(Application, HRInterviewSession.application_id == Application.application_id)
            .filter(
                Application.posting_id == posting.posting_id,
                HRInterviewSession.status == "Completed",
            )
            .order_by(HRInterviewSession.overall_score.desc().nullslast())
            .all()
        )

        updated = 0
        for rank, sess in enumerate(sessions, start=1):
            report = (
                db.query(HRInterviewReport)
                .filter(HRInterviewReport.session_id == sess.session_id)
                .first()
            )
            if report:
                report.rank_in_pool = rank
                updated += 1

        db.commit()
        logger.info(
            "[report_gen] Updated HR pool ranks for JR %d — %d reports.", requisition_id, updated
        )
        return updated

    except Exception:
        db.rollback()
        logger.exception("[report_gen] Failed to update HR pool ranks for JR %d.", requisition_id)
        return 0
    finally:
        db.close()


# ── Final candidate summary ───────────────────────────────────────────────────

def generate_final_candidate_summary(
    candidate_name: str,
    job_title: str,
    cv_score: float,
    tech_score: float,
    hr_score: float,
    final_score: float,
    final_rank: int,
    total_candidates: int,
    tech_tier: str,
    hr_tier: str,
    tech_strengths: list[str],
    hr_strengths: list[str],
    tech_weaknesses: list[str],
    hr_weaknesses: list[str],
    red_flag: bool = False,
    red_flag_reason: str = "",
) -> str:
    """
    Build a plain-text final candidate summary report (no LLM required).
    Returned string is stored in FinalRanking or emailed to HR.
    """
    overall_tier = _tier(final_score)
    rec          = _recommendation(final_score)

    cv_pct   = f"{cv_score:.1f}%"
    tech_pct = f"{tech_score:.1f}%"
    hr_pct   = f"{hr_score:.1f}%"
    fin_pct  = f"{final_score:.1f}%"

    rf_block = ""
    if red_flag:
        rf_block = (
            f"\n⚠ RED FLAG DETECTED\n"
            f"{'-'*40}\n"
            f"{red_flag_reason}\n"
        )

    strengths_block = ""
    if tech_strengths or hr_strengths:
        all_s = [f"[Technical] {s}" for s in tech_strengths[:2]] + \
                [f"[Behavioral] {s}" for s in hr_strengths[:2]]
        strengths_block = (
            f"\nKEY STRENGTHS\n"
            f"{chr(10).join('  • ' + s for s in all_s)}\n"
        )

    gaps_block = ""
    if tech_weaknesses or hr_weaknesses:
        all_w = [f"[Technical] {w}" for w in tech_weaknesses[:2]] + \
                [f"[Behavioral] {w}" for w in hr_weaknesses[:2]]
        gaps_block = (
            f"\nDEVELOPMENT AREAS\n"
            f"{chr(10).join('  • ' + w for w in all_w)}\n"
        )

    return (
        f"FINAL CANDIDATE REPORT\n"
        f"{'='*60}\n\n"
        f"Candidate:   {candidate_name}\n"
        f"Role:        {job_title}\n"
        f"Final Rank:  #{final_rank} of {total_candidates}\n\n"
        f"SCORE BREAKDOWN\n"
        f"{'-'*40}\n"
        f"  CV Screening (35%):          {cv_pct:>8}\n"
        f"  Technical Interview (40%):   {tech_pct:>8}  [{tech_tier}]\n"
        f"  HR Interview (25%):          {hr_pct:>8}  [{hr_tier}]\n"
        f"  {'─'*32}\n"
        f"  Final Weighted Score:        {fin_pct:>8}  [{overall_tier}]\n\n"
        f"RECOMMENDATION:  {rec}\n"
        f"{rf_block}"
        f"{strengths_block}"
        f"{gaps_block}"
    )

"""
Celery tasks for Technical Interview scheduling and Final Ranking.

Tasks
─────
send_interview_invitations(requisition_id)
    Worker — dispatched after relative grading completes.
    Creates TechnicalInterviewSession rows and sends signed interview links.
    Idempotent via jr.interview_notified flag.

maybe_dispatch_final_ranking(requisition_id)
    Lightweight check — called when a single interview session completes.
    Dispatches compute_final_ranking only when ALL sessions are terminal.

scan_and_dispatch_final_ranking()
    Beat task (every 5 min).
    Triggers when: status == 'interview_pending'
                   AND interview_deadline <= now
                   AND processing_status IN ('idle', 'error')
                   AND no FinalRanking rows exist yet

compute_final_ranking(requisition_id)
    Worker — delegates to the FinalRankingGraph in graphs/runners/.
    Acquires processing lock before running.
    Sets jr.status = 'ranking_complete' inside the graph node (persist_rankings_node).

Anti-loop guarantees
────────────────────
• send_interview_invitations: guarded by jr.interview_notified (True after first run)
• scan_and_dispatch_final_ranking: guarded by processing_status + FinalRanking existence
• compute_final_ranking: acquires acquire_jr_lock() before any work
• maybe_dispatch_final_ranking: guarded by FinalRanking existence check
"""

import logging
import time
from datetime import datetime, timedelta

from core.celery import celery_app
from core.pipeline_lock import acquire_jr_lock, release_jr_lock
from database.connection import SessionLocal
from graphs.runners.final_ranking_runner import run_final_ranking
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.candidate import Candidate
from models.db.final_ranking import FinalRanking
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_DEFAULT_INTERVIEW_CANDIDATES = 20
_INTERVIEW_DEADLINE_DAYS = 7
_BATCH_SIZE = 10
_BATCH_DELAY_SECONDS = 2


# ── Worker task: send interview invitations ───────────────────────────────────

@celery_app.task(
    name="tasks.interview_tasks.send_interview_invitations",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def send_interview_invitations(self, requisition_id: int) -> dict:
    """
    Creates TechnicalInterviewSession rows for non-rejected leaderboard entries
    and emails each candidate a signed interview link.

    Idempotent: skips if jr.interview_notified is already True.
    Sets jr.interview_notified = True BEFORE sending emails (prevents
    double-send on Celery retry).
    """
    from helpers.config import get_settings
    from services.email_service import send_interview_invitation_sync

    logger.info("[interview_inviter] Starting invitations for JR %d.", requisition_id)

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            logger.warning("[interview_inviter] JR %d not found.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        if jr.interview_notified:
            logger.info("[interview_inviter] JR %d already notified — skipping.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        config: TechnicalInterviewConfig = (
            db.query(TechnicalInterviewConfig)
            .filter(TechnicalInterviewConfig.requisition_id == requisition_id)
            .first()
        )
        if not config:
            config = TechnicalInterviewConfig(
                requisition_id=requisition_id,
                interview_type="AI Technical",
                duration_minutes=30,
                ai_feedback_level="detailed",
                scoring_system="centroid",
                candidates_to_advance=_DEFAULT_INTERVIEW_CANDIDATES,
            )
            db.add(config)
            db.flush()
            logger.info("[interview_inviter] Created default TechnicalInterviewConfig for JR %d.", requisition_id)

        limit = config.candidates_to_advance or _DEFAULT_INTERVIEW_CANDIDATES

        entries = (
            db.query(AssessmentLeaderboard)
            .filter(
                AssessmentLeaderboard.jr_id == requisition_id,
                AssessmentLeaderboard.reject == False,  # noqa: E712
            )
            .order_by(AssessmentLeaderboard.rank)
            .limit(limit)
            .all()
        )

        if not entries:
            logger.warning("[interview_inviter] No eligible candidates in leaderboard for JR %d.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"
        cfg = get_settings()
        base_url = cfg.APP_BASE_URL.rstrip("/")

        deadline_dt = datetime.utcnow() + timedelta(days=_INTERVIEW_DEADLINE_DAYS)
        deadline_str = deadline_dt.strftime("%B %d, %Y at %H:%M UTC")

        # Set guardrail BEFORE sending (prevents double-send on retry)
        jr.interview_notified = True
        jr.interview_deadline = deadline_dt
        jr.status = "interview_pending"
        db.flush()

        recipients: list[tuple[int, str, str]] = []
        for entry in entries:
            application: Application = (
                db.query(Application)
                .filter(Application.application_id == entry.application_id)
                .first()
            )
            if not application:
                continue
            candidate: Candidate = application.candidate
            if not candidate or not candidate.email:
                continue

            existing = (
                db.query(TechnicalInterviewSession)
                .filter(TechnicalInterviewSession.application_id == entry.application_id)
                .first()
            )
            if existing:
                continue

            session_row = TechnicalInterviewSession(
                application_id=entry.application_id,
                config_id=config.config_id,
                status="Scheduled",
                scheduled_at=deadline_dt,
            )
            db.add(session_row)
            recipients.append((entry.application_id, candidate.email, candidate.first_name or "Candidate"))

        db.commit()
        logger.info(
            "[interview_inviter] Created %d sessions for JR %d.", len(recipients), requisition_id,
        )

    except Exception as exc:
        db.rollback()
        logger.exception("[interview_inviter] DB error for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))
    finally:
        db.close()

    sent, failed = 0, 0
    for batch_start in range(0, len(recipients), _BATCH_SIZE):
        batch = recipients[batch_start: batch_start + _BATCH_SIZE]
        for app_id, email, first_name in batch:
            interview_url = f"{base_url}/interview?application_id={app_id}"
            success = send_interview_invitation_sync(
                recipient_email=email,
                first_name=first_name,
                job_title=job_title,
                interview_url=interview_url,
                interview_deadline=deadline_str,
            )
            if success:
                sent += 1
            else:
                failed += 1
        if batch_start + _BATCH_SIZE < len(recipients):
            time.sleep(_BATCH_DELAY_SECONDS)

    logger.info(
        "[interview_inviter] JR %d — %d invitations sent, %d failed.", requisition_id, sent, failed,
    )
    return {
        "requisition_id": requisition_id,
        "sessions_created": len(recipients),
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }


# ── Worker task: maybe trigger final ranking after a single session ───────────

@celery_app.task(name="tasks.interview_tasks.maybe_dispatch_final_ranking")
def maybe_dispatch_final_ranking(requisition_id: int) -> dict:
    """
    Called when one interview session completes.
    Dispatches compute_final_ranking only if ALL sessions for the JR are terminal.
    """
    _TERMINAL = {"Completed", "No-show", "Cancelled"}

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr or jr.status != "interview_pending":
            return {"dispatched": False, "reason": "JR not in interview_pending"}

        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return {"dispatched": False, "reason": "No posting"}

        sessions = (
            db.query(TechnicalInterviewSession)
            .join(Application, TechnicalInterviewSession.application_id == Application.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )
        if not sessions:
            return {"dispatched": False, "reason": "No sessions found"}

        if not all(s.status in _TERMINAL for s in sessions):
            return {"dispatched": False, "reason": "Some sessions still in progress"}

        # Guard: skip if final ranking already exists
        existing = (
            db.query(FinalRanking)
            .filter(FinalRanking.posting_id == posting.posting_id)
            .first()
        )
        if existing:
            return {"dispatched": False, "reason": "Final ranking already exists"}

    finally:
        db.close()

    compute_final_ranking.delay(requisition_id)
    logger.info("[interview_inviter] All sessions terminal for JR %d — dispatched final ranking.", requisition_id)
    return {"dispatched": True}


# ── Beat task: post-interview deadline scanner ────────────────────────────────

@celery_app.task(name="tasks.interview_tasks.scan_and_dispatch_final_ranking")
def scan_and_dispatch_final_ranking() -> dict:
    """
    Beat task (every 5 minutes).

    Triggers on JRs where:
      • status == 'interview_pending'
      • interview_deadline <= now
      • processing_status IN ('idle', 'error')   ← ANTI-LOOP
      • No FinalRanking row exists yet
    """
    dispatched: list[int] = []
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due_jobs: list[JobRequisition] = (
            db.query(JobRequisition)
            .filter(
                JobRequisition.status == "interview_pending",
                JobRequisition.interview_deadline.isnot(None),
                JobRequisition.interview_deadline <= now,
                # ANTI-LOOP: skip in-flight JRs
                JobRequisition.processing_status.in_(["idle", "error"]),
            )
            .all()
        )

        for jr in due_jobs:
            posting = (
                db.query(JobPosting)
                .filter(JobPosting.requisition_id == jr.requisition_id)
                .first()
            )
            if not posting:
                continue

            existing = (
                db.query(FinalRanking)
                .filter(FinalRanking.posting_id == posting.posting_id)
                .first()
            )
            if existing:
                continue

            logger.info(
                "[final_ranking_scanner] Interview deadline passed for JR %d — dispatching.",
                jr.requisition_id,
            )
            compute_final_ranking.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[final_ranking_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info(
        "[final_ranking_scanner] Dispatched final ranking for %d job(s): %s",
        len(dispatched), dispatched,
    )
    return {"dispatched": dispatched}


# ── Worker task: compute final ranking ───────────────────────────────────────

@celery_app.task(
    name="tasks.interview_tasks.compute_final_ranking",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def compute_final_ranking(self, requisition_id: int) -> dict:
    """
    Aggregates CV + assessment + interview scores into FinalRanking rows.

    Delegates to the FinalRankingGraph (graphs/runners/final_ranking_runner.py)
    which runs 6 nodes:
      gather_applications → compute_weights → score_candidates → sort_and_rank
      → persist_rankings (sets jr.status='ranking_complete') → send_decisions

    Acquires a processing lock before running to prevent duplicate invocations
    from both maybe_dispatch_final_ranking and scan_and_dispatch_final_ranking
    firing in the same time window.
    """
    logger.info("[final_ranker] Starting final ranking for JR %d.", requisition_id)

    # Fast idempotency check — if ranking_complete, nothing left to do
    db = SessionLocal()
    try:
        jr = db.query(JobRequisition).filter_by(requisition_id=requisition_id).first()
        if not jr:
            return {"requisition_id": requisition_id, "ranked": 0, "error": "JR not found"}
        if jr.status == "ranking_complete":
            logger.info("[final_ranker] JR %d already ranking_complete — skipping.", requisition_id)
            return {"requisition_id": requisition_id, "skipped": True}
    finally:
        db.close()

    acquired = acquire_jr_lock(requisition_id)
    if not acquired:
        logger.info(
            "[final_ranker] JR %d already processing — aborting duplicate.", requisition_id,
        )
        return {"requisition_id": requisition_id, "skipped": True, "reason": "already_processing"}

    result = {}
    try:
        result = run_final_ranking(requisition_id=requisition_id)
    except Exception as exc:
        release_jr_lock(requisition_id, success=False)
        logger.exception("[final_ranker] Unhandled exception for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    if result.get("error"):
        release_jr_lock(requisition_id, success=False)
        logger.error("[final_ranker] Graph error for JR %d: %s", requisition_id, result["error"])
        return {
            "requisition_id": requisition_id,
            "ranked": 0,
            "error": result["error"],
        }

    # persist_rankings_node already set jr.status = 'ranking_complete' and committed.
    # Release the lock to 'idle' in a clean separate session.
    release_jr_lock(requisition_id, success=True)

    scored = result.get("scored", [])
    red_flags = [r for r in scored if r.get("red_flag")]

    logger.info(
        "[final_ranker] JR %d complete — %d ranked, %d red flags, %d emails sent.",
        requisition_id,
        len(scored),
        len(red_flags),
        result.get("emails_sent", 0),
    )
    top_candidate_name = None
    if scored:
        c = scored[0].get("candidate")
        if c:
            first = getattr(c, "first_name", None) or ""
            last = getattr(c, "last_name", None) or ""
            top_candidate_name = f"{first} {last}".strip() or None

    return {
        "requisition_id": requisition_id,
        "ranked": len(scored),
        "red_flags": len(red_flags),
        "emails_sent": result.get("emails_sent", 0),
        "top_candidate": top_candidate_name,
        "top_score": scored[0]["weighted_total_score"] if scored else None,
    }

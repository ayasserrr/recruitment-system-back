"""
Celery tasks for automated CV ranking.

Tasks
─────
scan_and_dispatch_cv_ranking
    Beat task — runs every 5 minutes.
    Queries job_requisitions where:
        cv_collection_end_date <= today (UTC)
        AND status IN ('published', 'Active')     ← 'failed' deliberately excluded
        AND processing_status IN ('idle', 'error')  ← skip in-flight jobs
    Dispatches one process_cv_ranking task per matching row.

process_cv_ranking(requisition_id)
    Worker task — executes the full LangGraph AI ranking workflow.
    Acquires a processing lock on the JR before calling the graph so that
    concurrent Beat ticks cannot dispatch a second instance while the first
    is still running.

Idempotency
───────────
The ranking graph's persistence_node uses upserts.  Calling process_cv_ranking
twice for the same job is safe — all rank_in_pool values are recalculated.

Anti-loop guarantees
────────────────────
1. 'failed' is no longer in _RANKABLE_STATUSES.  A failed LinkedIn publish
   will NOT trigger infinite re-ranking.
2. The scanner skips any JR with processing_status == 'processing' (unless
   the lock is stale — older than STALE_LOCK_MINUTES in pipeline_lock.py).
3. The worker acquires the lock atomically using SELECT FOR UPDATE SKIP LOCKED,
   so two concurrent workers can never both believe they hold the lock.
4. On any exception the lock is released as 'error'; the scanner will then
   pick it up again on the next tick, limited by max_retries on the task.
"""

import logging
import time
from datetime import date

from core.celery import celery_app
from core.pipeline_lock import acquire_jr_lock, release_jr_lock
from database.connection import SessionLocal
from graphs.runners.cv_ranking_runner import run_cv_ranking
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from services.email_service import send_shortlist_notification_sync

logger = logging.getLogger(__name__)

# 'failed' removed intentionally — a failed LinkedIn publish should NOT
# re-trigger infinite ranking loops.  Operators must reset status manually.
_RANKABLE_STATUSES = {"published", "Active"}
_RANKED_STATUS = "ranked"


# ── Beat task: deadline scanner ───────────────────────────────────────────────

@celery_app.task(name="tasks.ranking_tasks.scan_and_dispatch_cv_ranking")
def scan_and_dispatch_cv_ranking() -> dict:
    """
    Periodic task (every 5 minutes).

    Finds every JobRequisition where:
      • cv_collection_end_date <= today            (deadline reached)
      • status IN ('published', 'Active')           (job was live)
      • processing_status IN ('idle', 'error')      (not currently in-flight)

    Dispatches one process_cv_ranking task per match.
    """
    today = date.today()
    dispatched: list[int] = []

    db = SessionLocal()
    try:
        due_jobs = (
            db.query(JobRequisition)
            .filter(
                JobRequisition.cv_collection_end_date.isnot(None),
                JobRequisition.cv_collection_end_date <= today,
                JobRequisition.status.in_(_RANKABLE_STATUSES),
                # ANTI-LOOP: skip JRs that are currently being processed
                JobRequisition.processing_status.in_(["idle", "error"]),
            )
            .all()
        )

        for jr in due_jobs:
            logger.info(
                "[ranking_scanner] Deadline passed for JR %d ('%s', deadline=%s) — "
                "dispatching ranking task.",
                jr.requisition_id,
                jr.job_title,
                jr.cv_collection_end_date,
            )
            process_cv_ranking.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[ranking_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info(
        "[ranking_scanner] Dispatched ranking for %d job(s): %s",
        len(dispatched), dispatched,
    )
    return {"dispatched": dispatched}


# ── Worker task: ranker ───────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.ranking_tasks.process_cv_ranking",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_cv_ranking(self, requisition_id: int) -> dict:
    """
    Worker task — one invocation per JobRequisition.

    Acquires a processing lock before running the graph.  If the lock is
    already held by a fresh (non-stale) run, the task exits immediately
    without doing any work, preventing duplicate ranking.

    Flow:
      1. Acquire processing lock (sets processing_status = 'processing')
      2. Run 6-node LangGraph ranking pipeline via graphs.runners
      3. On success → set jr.status = 'ranked', release lock (→ 'idle')
      4. On failure → release lock (→ 'error'), retry up to max_retries
    """
    logger.info("[ranking_worker] Starting CV ranking for JR %d.", requisition_id)

    # ANTI-LOOP: acquire the lock before doing any expensive work
    acquired = acquire_jr_lock(requisition_id)
    if not acquired:
        logger.info(
            "[ranking_worker] JR %d already processing — aborting duplicate dispatch.",
            requisition_id,
        )
        return {"requisition_id": requisition_id, "skipped": True, "reason": "already_processing"}

    result = {}
    try:
        result = run_cv_ranking(requisition_id=requisition_id)

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    except Exception as exc:
        release_jr_lock(requisition_id, success=False)
        logger.exception(
            "[ranking_worker] Unhandled exception for JR %d — retrying.", requisition_id
        )
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    # ── Graph-level error (logical, not transient) ────────────────────────────
    if result.get("error"):
        release_jr_lock(requisition_id, success=False)
        logger.error(
            "[ranking_worker] Ranking graph returned error for JR %d: %s",
            requisition_id, result["error"],
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "candidates_ranked": 0,
            "error": result["error"],
        }

    ranked = result.get("ranked_candidates", [])
    candidates_ranked = len(ranked)

    if candidates_ranked == 0:
        release_jr_lock(requisition_id, success=False)
        logger.warning(
            "[ranking_worker] JR %d has 0 ranked candidates — leaving status unchanged.",
            requisition_id,
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "candidates_ranked": 0,
            "error": "No applications found for this requisition.",
        }

    # ── Mark job as ranked (inside the same DB open as lock release) ──────────
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if jr:
            jr.status = _RANKED_STATUS
            jr.processing_status = "idle"
            jr.processing_started_at = None
            db.commit()
            logger.info(
                "[ranking_worker] JR %d status → 'ranked' (%d candidates).",
                requisition_id, candidates_ranked,
            )
    except Exception:
        db.rollback()
        release_jr_lock(requisition_id, success=False)
        logger.warning(
            "[ranking_worker] Could not update status for JR %d — ranking data saved "
            "but status unchanged.",
            requisition_id,
            exc_info=True,
        )
    finally:
        db.close()

    # ── Dispatch shortlist notification ───────────────────────────────────────
    send_shortlist_emails.delay(requisition_id)

    return {
        "requisition_id": requisition_id,
        "success": True,
        "candidates_ranked": candidates_ranked,
        "top_score": ranked[0].get("final_score") if ranked else None,
        "top_candidate": ranked[0].get("candidate_name") if ranked else None,
    }


# ── Worker task: shortlist notifier ──────────────────────────────────────────

_SHORTLIST_SIZE = 100
_BATCH_SIZE = 10
_BATCH_DELAY_SECONDS = 2


@celery_app.task(name="tasks.ranking_tasks.send_shortlist_emails", bind=True, max_retries=1)
def send_shortlist_emails(self, requisition_id: int) -> dict:
    """
    Selects the top-100 candidates (rank_in_pool <= 100), marks their
    applications as 'Shortlisted', and emails each in batches of 10.

    Guardrail: exits immediately if jr.shortlist_notified is already True,
    making it safe to re-run ranking without spamming candidates.
    """
    logger.info(
        "[shortlist_notifier] Starting for JR %d.", requisition_id,
    )

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )

        if not jr:
            logger.warning("[shortlist_notifier] JR %d not found — aborting.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        if jr.shortlist_notified:
            logger.info("[shortlist_notifier] JR %d already notified — skipping.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"

        rows = (
            db.query(SemanticAnalysisReport, Application, Candidate)
            .join(Application, SemanticAnalysisReport.application_id == Application.application_id)
            .join(Candidate, Application.candidate_id == Candidate.candidate_id)
            .join(JobPosting, Application.posting_id == JobPosting.posting_id)
            .filter(
                JobPosting.requisition_id == requisition_id,
                SemanticAnalysisReport.rank_in_pool <= _SHORTLIST_SIZE,
                Candidate.email.isnot(None),
                Candidate.email != "",
            )
            .order_by(SemanticAnalysisReport.rank_in_pool)
            .all()
        )

        if not rows:
            logger.info("[shortlist_notifier] No shortlistable candidates for JR %d.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        # Set guardrail BEFORE sending to prevent double-send on retry
        jr.shortlist_notified = True

        shortlisted_ids = [app.application_id for _, app, _ in rows]
        (
            db.query(Application)
            .filter(Application.application_id.in_(shortlisted_ids))
            .update({"status": "Shortlisted"}, synchronize_session=False)
        )

        not_shortlisted_id_rows = (
            db.query(Application.application_id)
            .join(SemanticAnalysisReport, SemanticAnalysisReport.application_id == Application.application_id)
            .join(JobPosting, Application.posting_id == JobPosting.posting_id)
            .filter(
                JobPosting.requisition_id == requisition_id,
                SemanticAnalysisReport.rank_in_pool > _SHORTLIST_SIZE,
            )
            .all()
        )
        not_shortlisted_ids = [row[0] for row in not_shortlisted_id_rows]
        not_shortlisted_count = 0
        if not_shortlisted_ids:
            not_shortlisted_count = (
                db.query(Application)
                .filter(Application.application_id.in_(not_shortlisted_ids))
                .update({"status": "Not Shortlisted"}, synchronize_session=False)
            )

        recipients = [(c.email, c.first_name) for _, _, c in rows]

        db.commit()
        logger.info(
            "[shortlist_notifier] JR %d — %d Shortlisted, %d Not Shortlisted.",
            requisition_id, len(shortlisted_ids), not_shortlisted_count,
        )

    except Exception as exc:
        db.rollback()
        logger.exception("[shortlist_notifier] DB error for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc)
    finally:
        db.close()

    sent, failed = 0, 0
    for batch_start in range(0, len(recipients), _BATCH_SIZE):
        batch = recipients[batch_start: batch_start + _BATCH_SIZE]
        for email, first_name in batch:
            success = send_shortlist_notification_sync(
                recipient_email=email,
                first_name=first_name,
                job_title=job_title,
                company_name=company_name,
            )
            if success:
                sent += 1
            else:
                failed += 1
        if batch_start + _BATCH_SIZE < len(recipients):
            time.sleep(_BATCH_DELAY_SECONDS)

    logger.info(
        "[shortlist_notifier] JR %d — %d sent, %d failed.", requisition_id, sent, failed,
    )

    from tasks.assessment_tasks import process_assessment_generation
    process_assessment_generation.delay(requisition_id)
    logger.info("[shortlist_notifier] Dispatched assessment generation for JR %d.", requisition_id)

    return {
        "requisition_id": requisition_id,
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }

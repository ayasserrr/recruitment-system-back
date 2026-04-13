"""
Celery tasks for automated CV ranking.

Tasks
─────
scan_and_dispatch_cv_ranking
    Beat task — runs every 5 minutes.
    Queries job_requisitions where:
        cv_collection_end_date <= today (UTC)   ← deadline has passed
        AND status NOT IN ('ranked', 'Draft', 'scheduled')
    Dispatches one process_cv_ranking task per matching row.

process_cv_ranking(requisition_id)
    Worker task — executes the full LangGraph AI ranking workflow for a
    single job requisition.  On success, sets jr.status = 'ranked' so
    the scanner never re-triggers the same job unless the status is
    manually reset (e.g. after the deadline is extended and new CVs arrive).

Idempotency
───────────
The ranking graph's persistence_node uses upserts, so calling
process_cv_ranking twice for the same job is safe — all rank_in_pool
values are simply recalculated and overwritten.

Triggering a re-rank after deadline extension
─────────────────────────────────────────────
  UPDATE job_requisitions
  SET    status = 'published',
         cv_collection_end_date = '<new_date>'
  WHERE  requisition_id = <jid>;

The scanner will pick it up on its next 5-minute tick once the new
date also passes.
"""

import logging
import time
from datetime import date

from core.celery import celery_app
from database.connection import SessionLocal
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from services.email_service import send_shortlist_notification_sync
from services.ranking_graph import run_ranking_graph

logger = logging.getLogger(__name__)

# Statuses that indicate a job is eligible for ranking
_RANKABLE_STATUSES = {"published", "Active", "failed"}

# Status written back after a successful rank run
_RANKED_STATUS = "ranked"


# ── Beat task: deadline scanner ───────────────────────────────────────────────

@celery_app.task(name="tasks.ranking_tasks.scan_and_dispatch_cv_ranking")
def scan_and_dispatch_cv_ranking() -> dict:
    """
    Periodic task (every 5 minutes).

    Finds every JobRequisition where:
      • cv_collection_end_date is set  AND  <= today   (deadline reached)
      • status is one of: 'published', 'Active', 'failed'
        (i.e. the job was live but is NOT already marked 'ranked')

    For each match, fires process_cv_ranking as an independent worker task
    so that one slow LLM call cannot block others.
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
    default_retry_delay=120,  # 2 minutes between retries
)
def process_cv_ranking(self, requisition_id: int) -> dict:
    """
    Worker task — one invocation per JobRequisition.

    Runs the full 6-node LangGraph ranking pipeline:
      1. context_gatherer      – loads JD + all CVs from DB (joinedload)
      2. deterministic_scoring – experience / education / skills / teamwork
      3. llm_qualitative       – GPT-4o-mini: project depth, strengths, concerns
      4. genai_validator        – GenAI evidence + deployment context
      5. final_ranker          – pool-relative labels + rank_in_pool
      6. persistence           – upserts semantic_analysis_reports + matched skills

    On success:
      • Sets JobRequisition.status = 'ranked' so the scanner skips it.

    On LangGraph error:
      • Retries up to 2 times with 2-minute delays.
      • After all retries are exhausted the job stays in its current
        status so an operator can investigate and re-trigger manually.

    Re-ranking after deadline extension:
      Reset jr.status back to 'published' (or 'Active') and update
      cv_collection_end_date to the new date.  The scanner will
      automatically dispatch a fresh ranking run once the new date passes.
    """
    logger.info(
        "[ranking_worker] Starting CV ranking for requisition %d.", requisition_id
    )

    try:
        result = run_ranking_graph(requisition_id=requisition_id)

    except Exception as exc:
        logger.exception(
            "[ranking_worker] Unhandled exception for requisition %d — retrying.",
            requisition_id,
        )
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    # ── Check graph-level error ───────────────────────────────────────────────
    if result.get("error"):
        logger.error(
            "[ranking_worker] Ranking graph returned error for requisition %d: %s",
            requisition_id,
            result["error"],
        )
        # Do NOT retry on graph errors (e.g. "no applications found") — they
        # are logical, not transient.  The job stays in its current status.
        return {
            "requisition_id": requisition_id,
            "success": False,
            "candidates_ranked": 0,
            "error": result["error"],
        }

    ranked = result.get("ranked_candidates", [])
    candidates_ranked = len(ranked)

    # ── No applications found — graph aborted early ───────────────────────────
    # context_gatherer found 0 applications → _route_after_context returned
    # "abort" → persistence_node never ran → ranked_candidates is empty but
    # error is also None.  Do NOT mark as 'ranked' and do NOT send emails;
    # leave the status unchanged so a re-rank attempt is possible once
    # candidates actually apply.
    if candidates_ranked == 0:
        logger.warning(
            "[ranking_worker] Requisition %d has 0 ranked candidates — "
            "no applications found or pipeline aborted early. "
            "Status unchanged; semantic_analysis_reports NOT written.",
            requisition_id,
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "candidates_ranked": 0,
            "error": "No applications found for this requisition.",
        }

    # ── Mark job as ranked ────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if jr:
            jr.status = _RANKED_STATUS
            db.commit()
            logger.info(
                "[ranking_worker] Requisition %d status → 'ranked' "
                "(%d candidates processed).",
                requisition_id,
                candidates_ranked,
            )
    except Exception:
        db.rollback()
        logger.warning(
            "[ranking_worker] Could not update status for requisition %d — "
            "ranking data was saved successfully but status remains unchanged.",
            requisition_id,
            exc_info=True,
        )
    finally:
        db.close()

    logger.info(
        "[ranking_worker] Completed requisition %d — %d candidates ranked.",
        requisition_id,
        candidates_ranked,
    )

    # ── Dispatch shortlist notification (non-blocking) ────────────────────────
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
_BATCH_DELAY_SECONDS = 2  # pause between batches to avoid SMTP rate-limiting


@celery_app.task(name="tasks.ranking_tasks.send_shortlist_emails", bind=True, max_retries=1)
def send_shortlist_emails(self, requisition_id: int) -> dict:
    """
    Worker task — dispatched immediately after a successful ranking run.

    Selects the top-100 candidates (rank_in_pool <= 100) for the given
    requisition, marks their applications as 'Shortlisted', and emails
    each of them in batches of 10 to avoid SMTP rate-limit flags.

    Guardrail: if JobRequisition.shortlist_notified is already True the
    task exits immediately without sending any email, making it safe to
    re-run ranking without spamming candidates.
    """
    logger.info(
        "[shortlist_notifier] Starting shortlist notifications for requisition %d.",
        requisition_id,
    )

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )

        if not jr:
            logger.warning(
                "[shortlist_notifier] Requisition %d not found — aborting.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        # ── Guardrail: fire only once per job ─────────────────────────────────
        if jr.shortlist_notified:
            logger.info(
                "[shortlist_notifier] Requisition %d already notified — skipping.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"

        # ── Fetch top-100 shortlisted candidates ──────────────────────────────
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
            logger.info(
                "[shortlist_notifier] No shortlistable candidates found for requisition %d.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        # ── Mark guardrail BEFORE sending (prevents double-send on retry) ─────
        jr.shortlist_notified = True

        # ── Update top-100 application statuses to 'Shortlisted' ─────────────
        shortlisted_ids = [app.application_id for _, app, _ in rows]
        (
            db.query(Application)
            .filter(Application.application_id.in_(shortlisted_ids))
            .update({"status": "Shortlisted"}, synchronize_session=False)
        )

        # ── Mark rank 101+ as 'Not Shortlisted' ──────────────────────────────
        # SQLAlchemy ORM does not support UPDATE...JOIN, so we fetch the IDs
        # first with a SELECT (join is fine there), then update by ID list.
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

        # Extract plain data before closing the session to avoid detached-instance issues
        recipients = [
            (candidate.email, candidate.first_name)
            for _, _, candidate in rows
        ]

        db.commit()
        logger.info(
            "[shortlist_notifier] Requisition %d — %d applications → 'Shortlisted', "
            "%d applications → 'Not Shortlisted'.",
            requisition_id,
            len(shortlisted_ids),
            not_shortlisted_count,
        )

    except Exception as exc:
        db.rollback()
        logger.exception(
            "[shortlist_notifier] DB error preparing shortlist for requisition %d — retrying.",
            requisition_id,
        )
        raise self.retry(exc=exc)
    finally:
        db.close()

    # ── Send emails in batches ────────────────────────────────────────────────
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

        # Pause between batches (skip after the last batch)
        if batch_start + _BATCH_SIZE < len(recipients):
            time.sleep(_BATCH_DELAY_SECONDS)

    logger.info(
        "[shortlist_notifier] Requisition %d — %d emails sent, %d failed.",
        requisition_id,
        sent,
        failed,
    )
    return {
        "requisition_id": requisition_id,
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }

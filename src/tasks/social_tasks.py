"""
Celery tasks for social-media publishing.

Tasks
─────
scan_and_dispatch_scheduled_posts
    Beat task — runs every minute.
    Queries job_requisitions where:
        status == 'scheduled'  AND  posting_start_date <= today (UTC)
    Dispatches one process_linkedin_publishing task per matching row.

process_linkedin_publishing(jr_id, company_id)
    Worker task — executes the LangGraph LinkedIn publishing workflow
    for a single job requisition.  Scoped to the specific company so
    credentials are never mixed across tenants.
"""

import logging
from datetime import date

from core.celery import celery_app
from database.connection import SessionLocal
from models.db.job_requisition import JobRequisition
from services.linkedin_graph import run_linkedin_publishing_graph

logger = logging.getLogger(__name__)


# ── Beat task: scanner ────────────────────────────────────────────────────────

@celery_app.task(name="tasks.social_tasks.scan_and_dispatch_scheduled_posts")
def scan_and_dispatch_scheduled_posts() -> dict:
    """
    Periodic task (every 60 s).

    Finds every JobRequisition that is:
      • status == 'scheduled'
      • posting_start_date is today or in the past

    For each match, fires process_linkedin_publishing as an independent
    worker task, passing (jr_id, company_id) so the worker is fully
    self-contained and multi-tenant safe.
    """
    today = date.today()
    dispatched: list[int] = []

    db = SessionLocal()
    try:
        due_jrs = (
            db.query(JobRequisition)
            .filter(
                JobRequisition.status == "Active",
                JobRequisition.posting_start_date <= today,
            )
            .all()
        )

        for jr in due_jrs:
            logger.info(
                "[scanner] Dispatching LinkedIn publish for JR %s "
                "(company=%s, posting_start_date=%s)",
                jr.requisition_id,
                jr.company_id,
                jr.posting_start_date,
            )
            process_linkedin_publishing.delay(jr.requisition_id, jr.company_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[scanner] Unexpected error while scanning scheduled posts")
    finally:
        db.close()

    logger.info("[scanner] Dispatched %d publishing task(s): %s", len(dispatched), dispatched)
    return {"dispatched": dispatched}


# ── Worker task: publisher ────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.social_tasks.process_linkedin_publishing",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # seconds between automatic retries
)
def process_linkedin_publishing(self, jr_id: int, company_id: int) -> dict:
    """
    Worker task — one invocation per JobRequisition.

    Delegates all business logic to the LangGraph workflow so that
    this task stays thin and the graph can be tested independently.

    Retry behaviour
    ───────────────
    If the graph raises an unexpected exception the task retries up to
    3 times (with exponential back-off).  Token-expiry and API errors
    are handled *inside* the graph and result in a status update to
    'failed' rather than a retry, because retrying with an expired
    token would always fail.
    """
    logger.info(
        "[publisher] Starting LinkedIn publishing for JR %s (company=%s)",
        jr_id,
        company_id,
    )

    try:
        result = run_linkedin_publishing_graph(jr_id, company_id)
        if result.get("success"):
            logger.info("[publisher] JR %s published successfully.", jr_id)
        else:
            logger.warning(
                "[publisher] JR %s publishing finished with error: %s",
                jr_id,
                result.get("error"),
            )
        return {"jr_id": jr_id, "success": result.get("success"), "error": result.get("error")}

    except Exception as exc:
        logger.exception("[publisher] Unhandled exception for JR %s — retrying.", jr_id)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))

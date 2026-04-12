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
from datetime import date

from core.celery import celery_app
from database.connection import SessionLocal
from models.db.job_requisition import JobRequisition
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
    return {
        "requisition_id": requisition_id,
        "success": True,
        "candidates_ranked": candidates_ranked,
        "top_score": ranked[0].get("final_score") if ranked else None,
        "top_candidate": ranked[0].get("candidate_name") if ranked else None,
    }

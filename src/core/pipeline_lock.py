"""
Pipeline concurrency lock for JobRequisition processing.

Problem solved
──────────────
Celery Beat fires every 60 s.  A full ranking or assessment pipeline can take
3–10 minutes.  Without a lock, the scanner dispatches N duplicate tasks before
the first one updates jr.status — each task then runs the expensive LLM
workflow and tries to commit the same data.

Solution
────────
Two columns on job_requisitions:
  processing_status     'idle' | 'processing' | 'error'
  processing_started_at  TIMESTAMP (UTC) — for stale-lock detection

Usage in a worker task
──────────────────────
    from core.pipeline_lock import acquire_jr_lock, release_jr_lock

    acquired = acquire_jr_lock(requisition_id)
    if not acquired:
        return {"skipped": True, "reason": "already_processing"}
    try:
        result = run_some_graph(requisition_id)
        # update status inside the finally block BEFORE releasing
    finally:
        release_jr_lock(requisition_id, success=not result.get("error"))

Usage in a scanner (Beat task)
──────────────────────────────
Add to the query filter:
    JobRequisition.processing_status.in_(["idle", "error"])
"""

import logging
from datetime import datetime, timedelta

from database.connection import SessionLocal
from models.db.job_requisition import JobRequisition

logger = logging.getLogger(__name__)

# A lock older than this is considered stale and can be overridden.
STALE_LOCK_MINUTES: int = 30


def acquire_jr_lock(requisition_id: int) -> bool:
    """
    Atomically mark a JobRequisition as 'processing'.

    Returns True  — lock acquired; caller may proceed.
    Returns False — JR is actively processing (lock is fresh); caller should abort.

    Stale lock override: if the previous processing_started_at is older than
    STALE_LOCK_MINUTES, the lock is considered abandoned (worker crash / kill)
    and is taken over.

    Uses SELECT FOR UPDATE SKIP LOCKED so two concurrent Celery workers never
    both believe they acquired the lock.
    """
    db = SessionLocal()
    try:
        jr = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if jr is None:
            # Either JR doesn't exist or another session has the row lock.
            logger.warning(
                "[pipeline_lock] Could not lock JR %d — row missing or contended.",
                requisition_id,
            )
            return False

        now = datetime.utcnow()
        if jr.processing_status == "processing":
            stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)
            if jr.processing_started_at and jr.processing_started_at > stale_cutoff:
                logger.info(
                    "[pipeline_lock] JR %d already processing (started %s) — skipping.",
                    requisition_id,
                    jr.processing_started_at,
                )
                return False
            logger.warning(
                "[pipeline_lock] Stale lock on JR %d (started %s, >%d min ago) — overriding.",
                requisition_id,
                jr.processing_started_at,
                STALE_LOCK_MINUTES,
            )

        jr.processing_status = "processing"
        jr.processing_started_at = now
        db.commit()
        logger.info("[pipeline_lock] Lock acquired for JR %d.", requisition_id)
        return True

    except Exception:
        db.rollback()
        logger.exception("[pipeline_lock] Unexpected error acquiring lock for JR %d.", requisition_id)
        return False
    finally:
        db.close()


def release_jr_lock(requisition_id: int, *, success: bool = True) -> None:
    """
    Release the processing lock on a JobRequisition.

    success=True  → processing_status = 'idle',  processing_started_at = NULL
    success=False → processing_status = 'error'  (preserves started_at for forensics)

    Opens a fresh session — safe to call after the main processing session is
    already closed or rolled back.
    """
    db = SessionLocal()
    try:
        jr = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            return
        jr.processing_status = "idle" if success else "error"
        if success:
            jr.processing_started_at = None
        db.commit()
        logger.info(
            "[pipeline_lock] Lock released for JR %d → processing_status='%s'.",
            requisition_id,
            jr.processing_status,
        )
    except Exception:
        db.rollback()
        logger.exception("[pipeline_lock] Error releasing lock for JR %d.", requisition_id)
    finally:
        db.close()


def is_locked(requisition_id: int) -> bool:
    """
    Non-locking probe — returns True if the JR has a fresh (non-stale) processing lock.
    Used by scanners before dispatching; uses a plain SELECT (no FOR UPDATE).
    """
    db = SessionLocal()
    try:
        jr = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr or jr.processing_status != "processing":
            return False
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)
        return bool(jr.processing_started_at and jr.processing_started_at > stale_cutoff)
    finally:
        db.close()

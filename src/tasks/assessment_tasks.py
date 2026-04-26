"""
Celery tasks for automated Technical Assessment generation.

Tasks
─────
scan_and_dispatch_assessment
    Beat task — runs every 60 s.
    Triggers when: status == 'ranked' AND shortlist_notified == True
                   AND TechnicalAssessmentConfig exists
                   AND processing_status IN ('idle', 'error')

    NOTE: The old guard checked CandidateAssessment existence (count > 0).
    That was fragile — a partial assessment creation would permanently block
    retries because count > 0 even though only some candidates had assessments.
    The new guard relies on jr.status == 'assessment_sent' (set atomically
    when the full graph completes) + the processing lock.

process_assessment_generation(requisition_id)
    Worker task — acquires lock, runs assessment graph, sets 'assessment_sent'.

scan_and_dispatch_assessment_ranking
    Beat task — triggers when: status == 'assessment_sent'
                                AND assessment_deadline <= now
                                AND pool_report IS NULL
                                AND processing_status IN ('idle', 'error')

process_assessment_ranking(requisition_id)
    Worker task — runs post-deadline graph, sets 'assessment_ranked'.

run_relative_grading(requisition_id)
    Worker task — 3-phase relative grading → AssessmentLeaderboard rows.
    Dispatches interview invitations when done.

Anti-loop guarantees
────────────────────
Every scanner checks processing_status IN ('idle', 'error') before dispatching.
Every worker acquires acquire_jr_lock() at the start.
Every worker releases the lock (success or error) at the end.
"""

import logging
from datetime import datetime, timedelta, timezone

from core.celery import celery_app
from core.pipeline_lock import acquire_jr_lock, release_jr_lock
from database.connection import SessionLocal
from graphs.runners.assessment_runner import run_assessment
from graphs.runners.post_deadline_runner import run_post_deadline
from models.db.application import Application
from models.db.assessment_report import AssessmentReport
from models.db.candidate_assessment import CandidateAssessment
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_assessment_config import TechnicalAssessmentConfig

logger = logging.getLogger(__name__)

_ASSESSMENT_STATUS = "assessment_sent"
_EXPIRE_AFTER_HOURS = 72


# ── Beat task: assessment generation scanner ──────────────────────────────────

@celery_app.task(name="tasks.assessment_tasks.scan_and_dispatch_assessment")
def scan_and_dispatch_assessment() -> dict:
    """
    Periodic task (every 60 s).

    Triggers on JRs where:
      • status == 'ranked'               (CV ranking completed)
      • shortlist_notified == True       (shortlist emails sent)
      • TechnicalAssessmentConfig exists (HR configured the assessment)
      • processing_status IN ('idle','error')  (not currently in-flight)

    Status-based guard (NOT CandidateAssessment count):
    The scanner only dispatches when jr.status == 'ranked'.  The worker
    transitions status to 'assessment_sent' on success, so subsequent
    scanner ticks find status != 'ranked' and skip automatically.
    """
    dispatched: list[int] = []
    db = SessionLocal()
    try:
        candidates = (
            db.query(JobRequisition)
            .join(
                TechnicalAssessmentConfig,
                TechnicalAssessmentConfig.requisition_id == JobRequisition.requisition_id,
            )
            .filter(
                JobRequisition.status == "ranked",
                JobRequisition.shortlist_notified == True,
                # ANTI-LOOP: skip in-flight JRs
                JobRequisition.processing_status.in_(["idle", "error"]),
            )
            .all()
        )

        for jr in candidates:
            logger.info(
                "[assessment_scanner] Dispatching assessment generation for JR %d ('%s').",
                jr.requisition_id, jr.job_title,
            )
            process_assessment_generation.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[assessment_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info(
        "[assessment_scanner] Dispatched assessment generation for %d job(s): %s",
        len(dispatched), dispatched,
    )
    return {"dispatched": dispatched}


# ── Worker task: assessment generator ────────────────────────────────────────

@celery_app.task(
    name="tasks.assessment_tasks.process_assessment_generation",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_assessment_generation(self, requisition_id: int) -> dict:
    """
    Worker task — runs the 4-node LangGraph assessment workflow.

    1. Acquires processing lock.
    2. Idempotency check: if status is already 'assessment_sent', skip.
    3. Runs the graph via graphs.runners.assessment_runner.
    4. On success: sets jr.status = 'assessment_sent', releases lock → 'idle'.
    5. On failure: releases lock → 'error', retries up to max_retries.
    """
    logger.info("[assessment_worker] Starting for JR %d.", requisition_id)

    # Fast idempotency check before acquiring lock
    db = SessionLocal()
    try:
        jr = db.query(JobRequisition).filter_by(requisition_id=requisition_id).first()
        if not jr:
            logger.warning("[assessment_worker] JR %d not found.", requisition_id)
            return {"requisition_id": requisition_id, "skipped": True}
        if jr.status == _ASSESSMENT_STATUS:
            logger.info(
                "[assessment_worker] JR %d already 'assessment_sent' — skipping.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "skipped": True, "reason": "already_sent"}
    finally:
        db.close()

    acquired = acquire_jr_lock(requisition_id)
    if not acquired:
        logger.info(
            "[assessment_worker] JR %d already processing — aborting duplicate.", requisition_id
        )
        return {"requisition_id": requisition_id, "skipped": True, "reason": "already_processing"}

    result = {}
    try:
        result = run_assessment(requisition_id=requisition_id)
    except Exception as exc:
        release_jr_lock(requisition_id, success=False)
        logger.exception("[assessment_worker] Unhandled exception for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    if result.get("error"):
        release_jr_lock(requisition_id, success=False)
        logger.error(
            "[assessment_worker] Graph error for JR %d: %s", requisition_id, result["error"]
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "assessments_created": 0,
            "error": result["error"],
        }

    assessments_created = len(result.get("assessment_ids", []))

    db = SessionLocal()
    try:
        jr = db.query(JobRequisition).filter_by(requisition_id=requisition_id).first()
        if jr:
            jr.status = _ASSESSMENT_STATUS
            jr.processing_status = "idle"
            jr.processing_started_at = None
            db.commit()
            logger.info(
                "[assessment_worker] JR %d status → 'assessment_sent' (%d assessments).",
                requisition_id, assessments_created,
            )
    except Exception:
        db.rollback()
        release_jr_lock(requisition_id, success=False)
        logger.warning("[assessment_worker] Could not update status for JR %d.", requisition_id, exc_info=True)
    finally:
        db.close()

    return {
        "requisition_id": requisition_id,
        "success": True,
        "assessments_created": assessments_created,
        "questions_generated": result.get("questions_generated", False),
        "template_id": result.get("template_id"),
    }


# ── Worker task: assessment rank recalculation (after each submission) ────────

@celery_app.task(name="tasks.assessment_tasks.recalculate_assessment_rankings")
def recalculate_assessment_rankings(requisition_id: int) -> dict:
    """
    Called after each candidate submits their assessment.

    Recalculates rank_in_pool for all Submitted assessments and updates
    CandidateAssessment.passed.  Idempotent — safe to call multiple times.
    """
    logger.info("[ranking_recalc] Recalculating ranks for JR %d.", requisition_id)

    db = SessionLocal()
    try:
        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            logger.warning("[ranking_recalc] No posting for JR %d.", requisition_id)
            return {"requisition_id": requisition_id, "ranked": 0}

        config = (
            db.query(TechnicalAssessmentConfig)
            .filter(TechnicalAssessmentConfig.requisition_id == requisition_id)
            .first()
        )

        submitted = (
            db.query(CandidateAssessment)
            .join(Application, CandidateAssessment.application_id == Application.application_id)
            .filter(
                Application.posting_id == posting.posting_id,
                CandidateAssessment.status == "Submitted",
                CandidateAssessment.total_score.isnot(None),
            )
            .order_by(CandidateAssessment.total_score.desc())
            .all()
        )

        if not submitted:
            return {"requisition_id": requisition_id, "ranked": 0}

        updated = 0
        for rank, assessment in enumerate(submitted, start=1):
            try:
                total_score = float(assessment.total_score or 0)
                if assessment.passing_score is not None:
                    assessment.passed = total_score >= float(assessment.passing_score)

                report = (
                    db.query(AssessmentReport)
                    .filter(AssessmentReport.assessment_id == assessment.assessment_id)
                    .first()
                )
                if report:
                    report.rank_in_pool = rank
                    updated += 1
            except Exception:
                logger.warning("[ranking_recalc] Error for assessment %d.", assessment.assessment_id, exc_info=True)

        db.commit()
        logger.info(
            "[ranking_recalc] JR %d — %d/%d reports updated.",
            requisition_id, updated, len(submitted),
        )
        return {"requisition_id": requisition_id, "ranked": len(submitted), "reports_updated": updated}

    except Exception:
        db.rollback()
        logger.exception("[ranking_recalc] DB error for JR %d.", requisition_id)
        return {"requisition_id": requisition_id, "ranked": 0, "error": "DB error"}
    finally:
        db.close()


# ── Beat task: expire stale assessments ──────────────────────────────────────

@celery_app.task(name="tasks.assessment_tasks.scan_and_expire_assessments")
def scan_and_expire_assessments() -> dict:
    """Beat task (every 30 min). Marks CandidateAssessment rows as 'Expired'."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=_EXPIRE_AFTER_HOURS)
    expired_ids: list[int] = []

    db = SessionLocal()
    try:
        stale = (
            db.query(CandidateAssessment)
            .filter(
                CandidateAssessment.status.in_(["Pending", "In Progress"]),
                CandidateAssessment.submitted_at.is_(None),
            )
            .all()
        )

        for assessment in stale:
            should_expire = False

            if assessment.started_at and assessment.config_id:
                try:
                    config = (
                        db.query(TechnicalAssessmentConfig)
                        .filter(TechnicalAssessmentConfig.config_id == assessment.config_id)
                        .first()
                    )
                    if config and config.time_limit_minutes:
                        deadline = assessment.started_at + timedelta(minutes=config.time_limit_minutes)
                        if now > deadline:
                            should_expire = True
                except Exception:
                    pass

            if not should_expire:
                reference_time = assessment.started_at or cutoff
                if reference_time <= cutoff:
                    should_expire = True

            if should_expire:
                assessment.status = "Expired"
                expired_ids.append(assessment.assessment_id)

        if expired_ids:
            db.commit()
            logger.info("[expiry_scanner] Expired %d stale assessments: %s", len(expired_ids), expired_ids)
        else:
            logger.debug("[expiry_scanner] No stale assessments found.")

    except Exception:
        db.rollback()
        logger.exception("[expiry_scanner] Error during expiry scan.")
    finally:
        db.close()

    return {"expired": expired_ids}


# ── Beat task: post-deadline pool processing scanner ─────────────────────────

@celery_app.task(name="tasks.assessment_tasks.scan_and_dispatch_assessment_ranking")
def scan_and_dispatch_assessment_ranking() -> dict:
    """
    Beat task (every 5 minutes).

    Triggers on JRs where:
      • status == 'assessment_sent'
      • assessment_deadline <= now
      • pool_report IS NULL              (not yet processed)
      • processing_status IN ('idle', 'error')
    """
    dispatched: list[int] = []
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        candidates = (
            db.query(JobRequisition)
            .join(
                TechnicalAssessmentConfig,
                TechnicalAssessmentConfig.requisition_id == JobRequisition.requisition_id,
            )
            .filter(
                JobRequisition.status == "assessment_sent",
                TechnicalAssessmentConfig.assessment_deadline.isnot(None),
                TechnicalAssessmentConfig.assessment_deadline <= now,
                TechnicalAssessmentConfig.pool_report.is_(None),
                # ANTI-LOOP
                JobRequisition.processing_status.in_(["idle", "error"]),
            )
            .all()
        )

        for jr in candidates:
            logger.info(
                "[ranking_scanner] Dispatching post-deadline ranking for JR %d ('%s').",
                jr.requisition_id, jr.job_title,
            )
            process_assessment_ranking.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[ranking_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info("[ranking_scanner] Dispatched post-deadline ranking for %d job(s).", len(dispatched))
    return {"dispatched": dispatched}


# ── Worker task: post-deadline pool processor ─────────────────────────────────

@celery_app.task(
    name="tasks.assessment_tasks.process_assessment_ranking",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_assessment_ranking(self, requisition_id: int) -> dict:
    """
    Worker task — runs post-deadline graph after assessment deadline passes.

    1. acquire_jr_lock
    2. run_post_deadline_graph (mark no-shows + rank pool + generate report)
    3. On success → set jr.status = 'assessment_ranked', release lock
    4. Dispatch run_relative_grading
    """
    logger.info("[ranking_worker] Starting post-deadline processing for JR %d.", requisition_id)

    acquired = acquire_jr_lock(requisition_id)
    if not acquired:
        return {"requisition_id": requisition_id, "skipped": True, "reason": "already_processing"}

    result = {}
    try:
        result = run_post_deadline(requisition_id=requisition_id)
    except Exception as exc:
        release_jr_lock(requisition_id, success=False)
        logger.exception("[ranking_worker] Unhandled exception for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    if result.get("error"):
        release_jr_lock(requisition_id, success=False)
        logger.error("[ranking_worker] Graph error for JR %d: %s", requisition_id, result["error"])
        return {"requisition_id": requisition_id, "success": False, "error": result["error"]}

    db = SessionLocal()
    try:
        jr = db.query(JobRequisition).filter_by(requisition_id=requisition_id).first()
        if jr:
            jr.status = "assessment_ranked"
            jr.processing_status = "idle"
            jr.processing_started_at = None
            db.commit()
            logger.info(
                "[ranking_worker] JR %d status → 'assessment_ranked' "
                "(%d no-shows, %d ranked).",
                requisition_id, result.get("no_show_count", 0), result.get("ranked_count", 0),
            )
    except Exception:
        db.rollback()
        release_jr_lock(requisition_id, success=False)
        logger.warning("[ranking_worker] Could not update status for JR %d.", requisition_id, exc_info=True)
    finally:
        db.close()

    run_relative_grading.delay(requisition_id)
    logger.info("[ranking_worker] Dispatched relative grading for JR %d.", requisition_id)

    return {
        "requisition_id": requisition_id,
        "success": True,
        "no_show_count": result.get("no_show_count", 0),
        "ranked_count": result.get("ranked_count", 0),
    }


# ── Worker task: 3-phase relative grading ────────────────────────────────────

@celery_app.task(
    name="tasks.assessment_tasks.run_relative_grading",
    bind=True,
    max_retries=2,
    default_retry_delay=180,
)
def run_relative_grading(self, requisition_id: int) -> dict:
    """
    3-phase relative auto-grading pipeline.

    Phase 1: Keyword coverage with GPT-4o-mini semantic fallback
    Phase 2: Comparative depth scoring (normalised vs pool max)
    Phase 3: Ranking, rejection, tiebreaking

    Saves results to assessment_leaderboards + hr_report.xlsx.
    Dispatches send_interview_invitations when done.
    Idempotent: clears previous leaderboard entries before writing new ones.
    """
    from services.relative_grading.logger import setup_grading_logger
    from services.relative_grading.runner import run_relative_grading_pipeline

    setup_grading_logger()
    logger.info("[relative_grading] Starting 3-phase pipeline for JR %d.", requisition_id)

    db = SessionLocal()
    try:
        result = run_relative_grading_pipeline(requisition_id, db)
    except Exception as exc:
        logger.exception("[relative_grading] Unhandled exception for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=180 * (self.request.retries + 1))
    finally:
        db.close()

    if result.get("error"):
        logger.error("[relative_grading] Pipeline error for JR %d: %s", requisition_id, result["error"])
        return {
            "requisition_id": requisition_id,
            "success": False,
            "error": result["error"],
        }

    logger.info(
        "[relative_grading] Done. JR=%d | passed=%d rejected=%d top=%s@%.3f",
        requisition_id,
        result["n_passed"],
        result["n_rejected"],
        result.get("top_candidate") or "—",
        result.get("top_score") or 0.0,
    )

    from tasks.interview_tasks import send_interview_invitations
    send_interview_invitations.delay(requisition_id)
    logger.info("[relative_grading] Dispatched interview invitations for JR %d.", requisition_id)

    return {
        "requisition_id": requisition_id,
        "success": True,
        "n_passed": result["n_passed"],
        "n_rejected": result["n_rejected"],
        "top_candidate": result.get("top_candidate"),
        "top_score": result.get("top_score"),
        "report_path": result.get("report_path"),
    }

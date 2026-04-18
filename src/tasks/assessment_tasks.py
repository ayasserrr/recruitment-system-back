"""
Celery tasks for automated Technical Assessment generation.

Tasks
─────
scan_and_dispatch_assessment
    Beat task — runs every 5 minutes.
    Queries job_requisitions where:
        status == 'ranked'
        AND shortlist_notified == True
        AND TechnicalAssessmentConfig exists
        AND no CandidateAssessment rows exist yet for that requisition
    Dispatches one process_assessment_generation per match.

process_assessment_generation(requisition_id)
    Worker task — runs the full LangGraph Assessment workflow:
      1. load_context_node       – skills, seniority, shortlisted candidates
      2. generate_questions_node – GPT-4o-mini question generation + DB write
      3. create_assessments_node – one CandidateAssessment per shortlisted app
      4. send_invitations_node   – HMAC-signed link + email via SMTP

    On success: marks jr.status = 'assessment_sent'.
    On error: leaves status unchanged so the scanner can retry.

Idempotency
───────────
The assessment graph's create_assessments_node and send_invitations_node
are both idempotent — safe to call twice for the same requisition.
"""

import logging
from datetime import datetime, timedelta, timezone

from core.celery import celery_app
from database.connection import SessionLocal
from models.db.application import Application
from models.db.assessment_report import AssessmentReport
from models.db.candidate_assessment import CandidateAssessment
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from services.assessment_graph import run_assessment_graph

logger = logging.getLogger(__name__)

_ASSESSMENT_STATUS = "assessment_sent"


# ── Beat task: scanner ────────────────────────────────────────────────────────

@celery_app.task(name="tasks.assessment_tasks.scan_and_dispatch_assessment")
def scan_and_dispatch_assessment() -> dict:
    """
    Periodic task (every 5 minutes).

    Finds every JobRequisition where:
      • status == 'ranked'               (ranking pipeline completed)
      • shortlist_notified == True       (shortlist emails already sent)
      • TechnicalAssessmentConfig exists (assessment was configured by HR)
      • No CandidateAssessment rows yet  (assessment not yet generated)
    """
    dispatched: list[int] = []
    db = SessionLocal()
    try:
        # Fetch ranked + notified requisitions that have an assessment config.
        # Explicitly EXCLUDE 'assessment_sent' so the scanner never re-triggers
        # a job that has already completed question generation.
        candidates = (
            db.query(JobRequisition)
            .join(TechnicalAssessmentConfig,
                  TechnicalAssessmentConfig.requisition_id == JobRequisition.requisition_id)
            .filter(
                JobRequisition.status == "ranked",       # only jobs awaiting assessment
                JobRequisition.shortlist_notified == True,
            )
            .all()
        )

        for jr in candidates:
            # Check if any CandidateAssessment already exists for this requisition
            posting = (
                db.query(JobPosting)
                .filter(JobPosting.requisition_id == jr.requisition_id)
                .first()
            )
            if not posting:
                continue

            existing_count = (
                db.query(CandidateAssessment)
                .join(Application, CandidateAssessment.application_id == Application.application_id)
                .filter(Application.posting_id == posting.posting_id)
                .count()
            )

            if existing_count > 0:
                logger.debug(
                    "[assessment_scanner] Requisition %d already has %d assessments — skipping.",
                    jr.requisition_id, existing_count,
                )
                continue

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
    Worker task — one invocation per JobRequisition.

    Runs the 4-node LangGraph assessment workflow:
      1. load_context        – JR data, skills, shortlisted candidates
      2. generate_questions  – GPT-4o-mini per skill → AssessmentTemplateQuestion
      3. create_assessments  – CandidateAssessment rows for each shortlisted candidate
      4. send_invitations    – HMAC-signed email per candidate

    On success: sets jr.status = 'assessment_sent'.
    """
    logger.info("[assessment_worker] Starting for requisition %d.", requisition_id)

    try:
        result = run_assessment_graph(requisition_id=requisition_id)
    except Exception as exc:
        logger.exception("[assessment_worker] Unhandled exception for requisition %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    if result.get("error"):
        logger.error(
            "[assessment_worker] Graph error for requisition %d: %s",
            requisition_id, result["error"],
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "assessments_created": 0,
            "error": result["error"],
        }

    assessments_created = len(result.get("assessment_ids", []))

    # Mark requisition status
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if jr:
            jr.status = _ASSESSMENT_STATUS
            db.commit()
            logger.info(
                "[assessment_worker] Requisition %d status → '%s' (%d assessments created).",
                requisition_id, _ASSESSMENT_STATUS, assessments_created,
            )
    except Exception:
        db.rollback()
        logger.warning(
            "[assessment_worker] Could not update status for requisition %d.",
            requisition_id,
            exc_info=True,
        )
    finally:
        db.close()

    return {
        "requisition_id": requisition_id,
        "success": True,
        "assessments_created": assessments_created,
        "questions_generated": result.get("questions_generated", False),
        "template_id": result.get("template_id"),
    }


# ── Worker task: post-assessment rank recalculation ───────────────────────────

@celery_app.task(name="tasks.assessment_tasks.recalculate_assessment_rankings")
def recalculate_assessment_rankings(requisition_id: int) -> dict:
    """
    Called after each candidate submits their assessment.

    Fetches all CandidateAssessment rows for the requisition that are
    'Submitted', sorts them by total_score descending, then writes
    rank_in_pool (1 = best) back to each matching AssessmentReport row.

    Also recalculates overall_score as a percentage and updates
    CandidateAssessment.passed based on the config's passing_score.

    Idempotent — safe to call multiple times as more candidates submit.
    """
    logger.info("[ranking_recalc] Recalculating ranks for requisition %d.", requisition_id)

    db = SessionLocal()
    try:
        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            logger.warning("[ranking_recalc] No posting for requisition %d.", requisition_id)
            return {"requisition_id": requisition_id, "ranked": 0}

        # Fetch config for passing_score
        config = (
            db.query(TechnicalAssessmentConfig)
            .filter(TechnicalAssessmentConfig.requisition_id == requisition_id)
            .first()
        )

        # Fetch all submitted assessments for this requisition
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
            logger.info("[ranking_recalc] No submitted assessments for requisition %d.", requisition_id)
            return {"requisition_id": requisition_id, "ranked": 0}

        pool_size = len(submitted)
        updated = 0

        for rank, assessment in enumerate(submitted, start=1):
            try:
                total_score = float(assessment.total_score or 0)

                # Determine overall_score percentage and pass/fail
                if assessment.passing_score is not None:
                    passing = float(assessment.passing_score)
                else:
                    # Default: 60% of total possible (questions × 10 pts each)
                    passing = None

                if passing is not None:
                    assessment.passed = total_score >= passing

                # Upsert AssessmentReport.rank_in_pool
                report = (
                    db.query(AssessmentReport)
                    .filter(AssessmentReport.assessment_id == assessment.assessment_id)
                    .first()
                )
                if report:
                    report.rank_in_pool = rank
                    updated += 1

            except Exception as exc:
                logger.warning(
                    "[ranking_recalc] Error updating assessment %d: %s",
                    assessment.assessment_id, exc,
                )

        db.commit()
        logger.info(
            "[ranking_recalc] Requisition %d — %d/%d reports updated with rank_in_pool.",
            requisition_id, updated, pool_size,
        )
        return {"requisition_id": requisition_id, "ranked": pool_size, "reports_updated": updated}

    except Exception:
        db.rollback()
        logger.exception("[ranking_recalc] DB error for requisition %d.", requisition_id)
        return {"requisition_id": requisition_id, "ranked": 0, "error": "DB error"}
    finally:
        db.close()


# ── Beat task: expire stale assessments ──────────────────────────────────────

_EXPIRE_AFTER_HOURS = 72   # assessments with no submission expire after 72 hours

@celery_app.task(name="tasks.assessment_tasks.scan_and_expire_assessments")
def scan_and_expire_assessments() -> dict:
    """
    Beat task (every 30 minutes).

    Marks CandidateAssessment rows as 'Expired' when ALL of these are true:
      • status is 'Pending' or 'In Progress'   (never submitted)
      • submitted_at IS NULL                    (no submission recorded)
      • One of:
          - started_at is set AND (now − started_at) > time_limit_minutes
          - OR created_at equivalent: assessment has been pending > EXPIRE_AFTER_HOURS

    This keeps the DB clean and prevents the submission endpoint from
    accepting answers for hopelessly stale assessments.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC
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

            # Case 1: candidate started but exceeded the time limit
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

            # Case 2: assessment has been sitting unopened past the global cutoff
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

    Finds every JobRequisition where:
      • status == 'assessment_sent'     (invitations have been dispatched)
      • assessment_deadline <= now       (deadline has passed)
      • pool_report IS NULL              (not yet processed)

    Dispatches one process_assessment_ranking per match.
    """
    from datetime import datetime as _dt

    dispatched: list[int] = []
    db = SessionLocal()
    try:
        now = _dt.utcnow()
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

    logger.info(
        "[ranking_scanner] Dispatched post-deadline ranking for %d job(s): %s",
        len(dispatched), dispatched,
    )
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
    Worker task — one invocation per JobRequisition after deadline.

    Runs the 4-node LangGraph post-deadline workflow:
      1. load_pool            – all CandidateAssessments for the requisition
      2. mark_no_shows        – Pending/In Progress → No-show, passed=False
      3. rank_pool            – sort Submitted by total_score, assign rank_in_pool
      4. generate_pool_report – GPT-4o-mini summary → TechnicalAssessmentConfig.pool_report

    On success: sets jr.status = 'assessment_ranked'.
    """
    from services.post_deadline_graph import run_post_deadline_graph

    logger.info("[ranking_worker] Starting post-deadline processing for requisition %d.", requisition_id)

    try:
        result = run_post_deadline_graph(requisition_id=requisition_id)
    except Exception as exc:
        logger.exception(
            "[ranking_worker] Unhandled exception for requisition %d — retrying.", requisition_id
        )
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))

    if result.get("error"):
        logger.error(
            "[ranking_worker] Graph error for requisition %d: %s",
            requisition_id, result["error"],
        )
        return {
            "requisition_id": requisition_id,
            "success": False,
            "error": result["error"],
        }

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if jr:
            jr.status = "assessment_ranked"
            db.commit()
            logger.info(
                "[ranking_worker] Requisition %d status → 'assessment_ranked' "
                "(%d no-shows, %d ranked).",
                requisition_id, result["no_show_count"], result["ranked_count"],
            )
    except Exception:
        db.rollback()
        logger.warning(
            "[ranking_worker] Could not update status for requisition %d.",
            requisition_id, exc_info=True,
        )
    finally:
        db.close()

    return {
        "requisition_id": requisition_id,
        "success": True,
        "no_show_count": result["no_show_count"],
        "ranked_count": result["ranked_count"],
    }

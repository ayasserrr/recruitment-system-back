"""
Celery tasks for Technical Interview → HR Interview → Final Ranking pipeline.

Status machine
──────────────
  interview_pending       (tech interviews running)
      ↓  all tech sessions terminal  OR  interview_deadline passes
  hr_interview_pending    (HR interviews running)
      ↓  all HR sessions terminal   OR  hr_interview_deadline passes
  ranking_complete        (final ranking computed)

Tasks
─────
send_interview_invitations(requisition_id)
    Worker — dispatched after relative grading.
    Creates TechnicalInterviewSession rows and emails signed interview links.
    Idempotent via jr.interview_notified.

maybe_dispatch_final_ranking(requisition_id)
    Lightweight check — called when a single TECH session ends (livekit_agent,
    interview_session webhook, interview.py route).
    When ALL tech sessions are terminal → dispatches send_hr_interview_invitations.

send_hr_interview_invitations(requisition_id)
    Worker — dispatched after all tech interviews complete.
    Creates HRInterviewSession rows and emails HR interview invitations.
    Idempotent via jr.hr_interview_notified.
    Sets jr.status = 'hr_interview_pending'.

maybe_dispatch_final_ranking_after_hr(requisition_id)
    Lightweight check — called when a single HR session score is submitted.
    When ALL HR sessions are terminal → dispatches compute_final_ranking.

scan_and_dispatch_hr_interviews()
    Beat task (every 5 min).
    Triggers when: status == 'interview_pending'
                   AND interview_deadline <= now
                   AND hr_interview_notified == False

scan_and_dispatch_final_ranking()
    Beat task (every 5 min).
    Triggers when: status == 'hr_interview_pending'
                   AND hr_interview_deadline <= now
                   AND no FinalRanking rows exist yet

compute_final_ranking(requisition_id)
    Worker — delegates to FinalRankingGraph.
    Sets jr.status = 'ranking_complete' inside persist_rankings_node.

Anti-loop guarantees
────────────────────
• send_interview_invitations:         guarded by jr.interview_notified
• send_hr_interview_invitations:      guarded by jr.hr_interview_notified
• scan_and_dispatch_hr_interviews:    guarded by hr_interview_notified
• scan_and_dispatch_final_ranking:    guarded by processing_status + FinalRanking existence
• compute_final_ranking:              acquires acquire_jr_lock()
• maybe_dispatch_final_ranking:       guarded by hr_interview_notified
• maybe_dispatch_final_ranking_after_hr: guarded by FinalRanking existence
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
from models.db.hr_interview_config import HRInterviewConfig
from models.db.hr_interview_session import HRInterviewSession
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_DEFAULT_INTERVIEW_CANDIDATES = 20
_DEFAULT_HR_CANDIDATES = 10
_INTERVIEW_DEADLINE_DAYS = 7
_HR_INTERVIEW_DEADLINE_DAYS = 5
_BATCH_SIZE = 10
_BATCH_DELAY_SECONDS = 2
_TERMINAL = {"Completed", "No-show", "Cancelled"}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_posting(db, requisition_id: int):
    return (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == requisition_id)
        .first()
    )


# ── Worker task: send TECH interview invitations ──────────────────────────────

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
    Idempotent via jr.interview_notified.
    """
    from helpers.config import get_settings
    from services.email_service import send_interview_invitation_sync

    logger.info("[tech_inviter] Starting tech invitations for JR %d.", requisition_id)

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            logger.warning("[tech_inviter] JR %d not found.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        if jr.interview_notified:
            logger.info("[tech_inviter] JR %d already notified — skipping.", requisition_id)
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
            logger.warning("[tech_inviter] No eligible candidates for JR %d.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"
        cfg = get_settings()
        base_url = cfg.APP_BASE_URL.rstrip("/")
        deadline_dt = datetime.utcnow() + timedelta(days=_INTERVIEW_DEADLINE_DAYS)
        deadline_str = deadline_dt.strftime("%B %d, %Y at %H:%M UTC")

        jr.interview_notified = True
        jr.interview_deadline = deadline_dt
        jr.status = "interview_pending"
        db.flush()

        recipients: list[tuple] = []
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
            recipients.append((
                entry.application_id,
                candidate.candidate_id,
                candidate.email,
                candidate.first_name or "Candidate",
            ))

        db.commit()
        logger.info("[tech_inviter] Created %d sessions for JR %d.", len(recipients), requisition_id)

    except Exception as exc:
        db.rollback()
        logger.exception("[tech_inviter] DB error for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))
    finally:
        db.close()

    sent, failed = 0, 0
    for batch_start in range(0, len(recipients), _BATCH_SIZE):
        batch = recipients[batch_start: batch_start + _BATCH_SIZE]
        for app_id, candidate_id, email, first_name in batch:
            interview_url = (
                f"{cfg.APP_BASE_URL.rstrip('/')}/interview"
                f"?requisition_id={requisition_id}&candidate_id={candidate_id}"
            )
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

    logger.info("[tech_inviter] JR %d — %d sent, %d failed.", requisition_id, sent, failed)
    return {
        "requisition_id": requisition_id,
        "sessions_created": len(recipients),
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }


# ── Worker task: check tech sessions → dispatch HR invitations ────────────────

@celery_app.task(name="tasks.interview_tasks.maybe_dispatch_final_ranking")
def maybe_dispatch_final_ranking(requisition_id: int) -> dict:
    """
    Called when one TECH interview session completes (livekit_agent, webhooks).
    If ALL tech sessions are now terminal → dispatches send_hr_interview_invitations.
    """
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr or jr.status != "interview_pending":
            return {"dispatched": False, "reason": "JR not in interview_pending"}

        if jr.hr_interview_notified:
            return {"dispatched": False, "reason": "HR invitations already sent"}

        posting = _get_posting(db, requisition_id)
        if not posting:
            return {"dispatched": False, "reason": "No posting"}

        sessions = (
            db.query(TechnicalInterviewSession)
            .join(Application, TechnicalInterviewSession.application_id == Application.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )
        if not sessions:
            return {"dispatched": False, "reason": "No tech sessions found"}

        if not all(s.status in _TERMINAL for s in sessions):
            return {"dispatched": False, "reason": "Some tech sessions still in progress"}

    finally:
        db.close()

    send_hr_interview_invitations.delay(requisition_id)
    logger.info("[tech_done] All tech sessions terminal for JR %d — dispatched HR invitations.", requisition_id)
    return {"dispatched": True}


# ── Worker task: send HR interview invitations ────────────────────────────────

@celery_app.task(
    name="tasks.interview_tasks.send_hr_interview_invitations",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def send_hr_interview_invitations(self, requisition_id: int) -> dict:
    """
    Creates HRInterviewSession rows for the top-N tech interview completers
    and emails each candidate an HR interview invitation.
    Idempotent via jr.hr_interview_notified.
    Sets jr.status = 'hr_interview_pending'.
    """
    from helpers.config import get_settings
    from services.email_service import send_hr_interview_invitation_sync

    logger.info("[hr_inviter] Starting HR invitations for JR %d.", requisition_id)

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            logger.warning("[hr_inviter] JR %d not found.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        if jr.hr_interview_notified:
            logger.info("[hr_inviter] JR %d already HR-notified — skipping.", requisition_id)
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        posting = _get_posting(db, requisition_id)
        if not posting:
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        # Auto-create HRInterviewConfig if missing
        config: HRInterviewConfig = (
            db.query(HRInterviewConfig)
            .filter(HRInterviewConfig.requisition_id == requisition_id)
            .first()
        )
        if not config:
            config = HRInterviewConfig(
                requisition_id=requisition_id,
                interview_type="HR",
                duration_minutes=45,
                scoring_system="1-10",
                candidates_to_advance=_DEFAULT_HR_CANDIDATES,
            )
            db.add(config)
            db.flush()

        limit = config.candidates_to_advance or _DEFAULT_HR_CANDIDATES

        # Pick top-N by tech overall_score among Completed sessions
        completed_sessions = (
            db.query(TechnicalInterviewSession)
            .join(Application, TechnicalInterviewSession.application_id == Application.application_id)
            .filter(
                Application.posting_id == posting.posting_id,
                TechnicalInterviewSession.status == "Completed",
            )
            .order_by(TechnicalInterviewSession.overall_score.desc().nullslast())
            .limit(limit)
            .all()
        )

        if not completed_sessions:
            logger.warning("[hr_inviter] No completed tech sessions for JR %d.", requisition_id)
            # Still advance to HR pending so deadline scanner doesn't re-fire
            jr.hr_interview_notified = True
            jr.hr_interview_deadline = datetime.utcnow() + timedelta(days=_HR_INTERVIEW_DEADLINE_DAYS)
            jr.status = "hr_interview_pending"
            db.commit()
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        cfg = get_settings()
        base_url = cfg.APP_BASE_URL.rstrip("/")
        deadline_dt = datetime.utcnow() + timedelta(days=_HR_INTERVIEW_DEADLINE_DAYS)
        deadline_str = deadline_dt.strftime("%B %d, %Y at %H:%M UTC")
        job_title = jr.job_title

        # Set guardrail BEFORE sending
        jr.hr_interview_notified = True
        jr.hr_interview_deadline = deadline_dt
        jr.status = "hr_interview_pending"
        db.flush()

        recipients: list[tuple] = []
        for tech_session in completed_sessions:
            application: Application = (
                db.query(Application)
                .filter(Application.application_id == tech_session.application_id)
                .first()
            )
            if not application:
                continue
            candidate: Candidate = application.candidate
            if not candidate or not candidate.email:
                continue

            # Skip if HR session already exists
            existing_hr = (
                db.query(HRInterviewSession)
                .filter(HRInterviewSession.application_id == tech_session.application_id)
                .first()
            )
            if existing_hr:
                continue

            hr_session = HRInterviewSession(
                application_id=tech_session.application_id,
                config_id=config.config_id,
                status="Scheduled",
                scheduled_at=deadline_dt,
            )
            db.add(hr_session)
            interview_url = (
                f"{base_url}/hr-interview"
                f"?requisition_id={requisition_id}&candidate_id={candidate.candidate_id}"
            )
            recipients.append((
                candidate.candidate_id,
                candidate.email,
                candidate.first_name or "Candidate",
                interview_url,
            ))

        db.commit()
        logger.info("[hr_inviter] Created %d HR sessions for JR %d.", len(recipients), requisition_id)

    except Exception as exc:
        db.rollback()
        logger.exception("[hr_inviter] DB error for JR %d — retrying.", requisition_id)
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))
    finally:
        db.close()

    sent, failed = 0, 0
    for batch_start in range(0, len(recipients), _BATCH_SIZE):
        batch = recipients[batch_start: batch_start + _BATCH_SIZE]
        for candidate_id, email, first_name, interview_url in batch:
            success = send_hr_interview_invitation_sync(
                recipient_email=email,
                first_name=first_name,
                job_title=job_title,
                interview_deadline=deadline_str,
                interview_url=interview_url,
            )
            if success:
                sent += 1
            else:
                failed += 1
        if batch_start + _BATCH_SIZE < len(recipients):
            time.sleep(_BATCH_DELAY_SECONDS)

    logger.info("[hr_inviter] JR %d — %d HR invitations sent, %d failed.", requisition_id, sent, failed)
    return {
        "requisition_id": requisition_id,
        "sessions_created": len(recipients),
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }


# ── Worker task: check HR sessions → dispatch final ranking ──────────────────

@celery_app.task(name="tasks.interview_tasks.maybe_dispatch_final_ranking_after_hr")
def maybe_dispatch_final_ranking_after_hr(requisition_id: int) -> dict:
    """
    Called when one HR interview score is submitted.
    If ALL HR sessions are now terminal → dispatches compute_final_ranking.
    """
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr or jr.status != "hr_interview_pending":
            return {"dispatched": False, "reason": "JR not in hr_interview_pending"}

        posting = _get_posting(db, requisition_id)
        if not posting:
            return {"dispatched": False, "reason": "No posting"}

        sessions = (
            db.query(HRInterviewSession)
            .join(Application, HRInterviewSession.application_id == Application.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )
        if not sessions:
            return {"dispatched": False, "reason": "No HR sessions found"}

        if not all(s.status in _TERMINAL for s in sessions):
            return {"dispatched": False, "reason": "Some HR sessions still in progress"}

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
    logger.info("[hr_done] All HR sessions terminal for JR %d — dispatched final ranking.", requisition_id)
    return {"dispatched": True}


# ── Beat task: tech deadline scanner → sends HR invitations ──────────────────

@celery_app.task(name="tasks.interview_tasks.scan_and_dispatch_hr_interviews")
def scan_and_dispatch_hr_interviews() -> dict:
    """
    Beat task (every 5 minutes).

    Triggers on JRs where:
      • status == 'interview_pending'
      • interview_deadline <= now
      • hr_interview_notified == False   ← ANTI-LOOP
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
                JobRequisition.hr_interview_notified == False,  # noqa: E712
            )
            .all()
        )

        for jr in due_jobs:
            logger.info(
                "[hr_scanner] Tech deadline passed for JR %d — dispatching HR invitations.",
                jr.requisition_id,
            )
            send_hr_interview_invitations.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[hr_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info("[hr_scanner] Dispatched HR invitations for %d job(s): %s", len(dispatched), dispatched)
    return {"dispatched": dispatched}


# ── Beat task: HR deadline scanner → triggers final ranking ──────────────────

@celery_app.task(name="tasks.interview_tasks.scan_and_dispatch_final_ranking")
def scan_and_dispatch_final_ranking() -> dict:
    """
    Beat task (every 5 minutes).

    Triggers on JRs where:
      • status == 'hr_interview_pending'
      • hr_interview_deadline <= now
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
                JobRequisition.status == "hr_interview_pending",
                JobRequisition.hr_interview_deadline.isnot(None),
                JobRequisition.hr_interview_deadline <= now,
                JobRequisition.processing_status.in_(["idle", "error"]),
            )
            .all()
        )

        for jr in due_jobs:
            posting = _get_posting(db, jr.requisition_id)
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
                "[final_ranking_scanner] HR deadline passed for JR %d — dispatching final ranking.",
                jr.requisition_id,
            )
            compute_final_ranking.delay(jr.requisition_id)
            dispatched.append(jr.requisition_id)

    except Exception:
        logger.exception("[final_ranking_scanner] Unexpected error during scan.")
    finally:
        db.close()

    logger.info("[final_ranking_scanner] Dispatched final ranking for %d job(s): %s", len(dispatched), dispatched)
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
    Aggregates CV + assessment + tech interview + HR interview scores into FinalRanking rows.
    Delegates to FinalRankingGraph (graphs/runners/final_ranking_runner.py).
    Acquires a processing lock before running to prevent duplicate invocations.
    """
    logger.info("[final_ranker] Starting final ranking for JR %d.", requisition_id)

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
        logger.info("[final_ranker] JR %d already processing — aborting duplicate.", requisition_id)
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
        return {"requisition_id": requisition_id, "ranked": 0, "error": result["error"]}

    release_jr_lock(requisition_id, success=True)

    scored = result.get("scored", [])
    red_flags = [r for r in scored if r.get("red_flag")]

    logger.info(
        "[final_ranker] JR %d complete — %d ranked, %d red flags, %d emails sent.",
        requisition_id, len(scored), len(red_flags), result.get("emails_sent", 0),
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

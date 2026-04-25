"""
Celery tasks for automated Technical Interview scheduling and Final Ranking.

Tasks
─────
send_interview_invitations(requisition_id)
    Worker task — dispatched after relative grading completes.

    Selects all non-rejected candidates from assessment_leaderboards
    (segment: "✅ Human Review" or "🟡 Shortlist"), capped by
    TechnicalInterviewConfig.candidates_to_advance (default 20).

    For each:
      • Creates a TechnicalInterviewSession row (status='Scheduled')
      • Sends a live interview invitation email with a signed link
    Sets jr.interview_notified = True and jr.interview_deadline = now+7 days.
    Idempotent: skips if jr.interview_notified is already True.

maybe_dispatch_final_ranking(requisition_id)
    Lightweight check — called when a single interview completes.
    Dispatches compute_final_ranking only if ALL sessions for the JR
    are in a terminal state (Completed / No-show / Cancelled).

scan_and_dispatch_final_ranking()
    Beat task (every 5 minutes).
    Finds every JR where:
      • status == 'interview_pending'
      • interview_deadline <= now  (hard deadline has passed)
      • No FinalRanking row yet exists for any application in this JR
    Dispatches compute_final_ranking per match.

compute_final_ranking(requisition_id)
    Worker task — aggregates every available score into FinalRanking rows.

    Weights (redistribute proportionally when a component is missing):
      • CV semantic score       60 %
      • Assessment score        25 %  (skipped if no assessments for JR)
      • Technical interview     15 %  (skipped if no sessions completed)

    Red-flag rule:
      Candidate with semantic_score >= 80 % AND technical_interview_score < 40 %
      → red_flag = True with reason.

    On success:
      • Upserts final_rankings rows
      • Assigns final_rank (1 = best)
      • Sets jr.status = 'ranking_complete'
      • Sends hire/no-hire decision emails to all candidates
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from core.celery import celery_app
from database.connection import SessionLocal
from models.db.application import Application
from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.final_ranking import FinalRanking
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

_DEFAULT_INTERVIEW_CANDIDATES = 20
_INTERVIEW_DEADLINE_DAYS = 7
_BATCH_SIZE = 10
_BATCH_DELAY_SECONDS = 2

# Red-flag thresholds
_RED_FLAG_CV_MIN = 80.0       # CV score must be ≥ this to be flagged
_RED_FLAG_INTERVIEW_MAX = 40.0  # Interview score must be < this to trigger flag


# ── Worker task: send interview invitations ───────────────────────────────────

@celery_app.task(
    name="tasks.interview_tasks.send_interview_invitations",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def send_interview_invitations(self, requisition_id: int) -> dict:
    """
    Dispatched after run_relative_grading completes.

    Selects top non-rejected candidates from assessment_leaderboards,
    creates TechnicalInterviewSession rows, and emails each candidate
    a signed interview link.
    """
    from helpers.config import get_settings
    from services.email_service import send_interview_invitation_sync

    logger.info(
        "[interview_inviter] Starting interview invitations for requisition %d.",
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
                "[interview_inviter] Requisition %d not found.", requisition_id
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        if jr.interview_notified:
            logger.info(
                "[interview_inviter] Requisition %d already interview-notified — skipping.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": True}

        # Resolve or create TechnicalInterviewConfig
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
            logger.info(
                "[interview_inviter] Created default TechnicalInterviewConfig for JR %d.",
                requisition_id,
            )

        limit = config.candidates_to_advance or _DEFAULT_INTERVIEW_CANDIDATES

        # Fetch non-rejected leaderboard entries, best-ranked first
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
            logger.warning(
                "[interview_inviter] No eligible candidates in leaderboard for JR %d.",
                requisition_id,
            )
            return {"requisition_id": requisition_id, "sent": 0, "skipped": False}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"
        cfg = get_settings()
        base_url = cfg.APP_BASE_URL.rstrip("/")

        deadline_dt = datetime.utcnow() + timedelta(days=_INTERVIEW_DEADLINE_DAYS)
        deadline_str = deadline_dt.strftime("%B %d, %Y at %H:%M UTC")

        # Mark guardrail + deadline BEFORE sending (idempotency on retry)
        jr.interview_notified = True
        jr.interview_deadline = deadline_dt
        jr.status = "interview_pending"
        db.flush()

        # Collect (application_id, email, first_name) triples
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

            # Skip if session already exists (idempotency)
            existing = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == entry.application_id
                )
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
            "[interview_inviter] Created %d interview sessions for JR %d.",
            len(recipients), requisition_id,
        )

    except Exception as exc:
        db.rollback()
        logger.exception(
            "[interview_inviter] DB error for requisition %d — retrying.", requisition_id
        )
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))
    finally:
        db.close()

    # ── Send invitation emails in batches ─────────────────────────────────────
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
        "[interview_inviter] Requisition %d — %d interview invitations sent, %d failed.",
        requisition_id, sent, failed,
    )
    return {
        "requisition_id": requisition_id,
        "sessions_created": len(recipients),
        "sent": sent,
        "failed": failed,
        "skipped": False,
    }


# ── Worker task: maybe trigger final ranking after single session completes ───

@celery_app.task(name="tasks.interview_tasks.maybe_dispatch_final_ranking")
def maybe_dispatch_final_ranking(requisition_id: int) -> dict:
    """
    Called when one interview session completes.

    Checks whether ALL TechnicalInterviewSession rows for this JR are in
    terminal states. If yes → dispatch compute_final_ranking immediately
    (don't wait for the deadline beat to fire).
    """
    _TERMINAL = {"Completed", "No-show", "Cancelled"}

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr or jr.status not in ("interview_pending",):
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
            .join(
                Application,
                TechnicalInterviewSession.application_id == Application.application_id,
            )
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )

        if not sessions:
            return {"dispatched": False, "reason": "No sessions found"}

        all_done = all(s.status in _TERMINAL for s in sessions)
        if not all_done:
            return {"dispatched": False, "reason": "Some sessions still in progress"}

        # Check final ranking not already computed
        existing_ranking = (
            db.query(FinalRanking)
            .filter(FinalRanking.posting_id == posting.posting_id)
            .first()
        )
        if existing_ranking:
            return {"dispatched": False, "reason": "Final ranking already exists"}

    finally:
        db.close()

    compute_final_ranking.delay(requisition_id)
    logger.info(
        "[interview_inviter] All sessions terminal for JR %d — dispatched final ranking.",
        requisition_id,
    )
    return {"dispatched": True}


# ── Beat task: post-interview deadline scanner ────────────────────────────────

@celery_app.task(name="tasks.interview_tasks.scan_and_dispatch_final_ranking")
def scan_and_dispatch_final_ranking() -> dict:
    """
    Beat task (every 5 minutes).

    Finds every JobRequisition where:
      • status == 'interview_pending'
      • interview_deadline <= now  (deadline has passed)
      • No FinalRanking row exists yet (not already processed)
    Dispatches one compute_final_ranking per match.
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

            # Guard: skip if ranking already computed
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

_WEIGHT_CV = 0.60
_WEIGHT_ASSESSMENT = 0.25
_WEIGHT_INTERVIEW = 0.15


@celery_app.task(
    name="tasks.interview_tasks.compute_final_ranking",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def compute_final_ranking(self, requisition_id: int) -> dict:
    """
    Aggregates CV + assessment + interview scores into FinalRanking rows.

    Weights are redistributed proportionally when a component is missing
    for the entire pool (e.g. no interviews conducted → CV+assessment only).

    Red-flag: semantic_score >= 80 AND interview_score < 40.
    On success: sets jr.status = 'ranking_complete', sends hire/no-hire emails.
    """
    from services.email_service import send_final_decision_sync

    logger.info(
        "[final_ranker] Starting final ranking for requisition %d.", requisition_id
    )

    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            logger.warning("[final_ranker] Requisition %d not found.", requisition_id)
            return {"requisition_id": requisition_id, "ranked": 0, "error": "JR not found"}

        posting: JobPosting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return {"requisition_id": requisition_id, "ranked": 0, "error": "No posting"}

        job_title = jr.job_title
        company_name = jr.company.name if jr.company else "Our Company"

        # ── Gather all shortlisted applications ───────────────────────────────
        applications: list[Application] = (
            db.query(Application)
            .filter(
                Application.posting_id == posting.posting_id,
                Application.status.in_(["Shortlisted", "interview_pending"]),
            )
            .all()
        )
        if not applications:
            # Fallback: any application with a semantic report
            applications = (
                db.query(Application)
                .join(
                    SemanticAnalysisReport,
                    SemanticAnalysisReport.application_id == Application.application_id,
                )
                .filter(Application.posting_id == posting.posting_id)
                .all()
            )

        if not applications:
            logger.warning(
                "[final_ranker] No applications found for requisition %d.", requisition_id
            )
            return {"requisition_id": requisition_id, "ranked": 0, "error": "No applications"}

        # ── Determine which score components exist at pool level ──────────────
        has_assessment = (
            db.query(AssessmentLeaderboard)
            .filter(AssessmentLeaderboard.jr_id == requisition_id)
            .first()
        ) is not None

        has_interview = (
            db.query(TechnicalInterviewSession)
            .join(
                Application,
                TechnicalInterviewSession.application_id == Application.application_id,
            )
            .filter(
                Application.posting_id == posting.posting_id,
                TechnicalInterviewSession.status == "Completed",
                TechnicalInterviewSession.overall_score.isnot(None),
            )
            .first()
        ) is not None

        # Compute effective weights (redistribute missing components to CV)
        w_cv = _WEIGHT_CV
        w_assessment = _WEIGHT_ASSESSMENT if has_assessment else 0.0
        w_interview = _WEIGHT_INTERVIEW if has_interview else 0.0
        total_w = w_cv + w_assessment + w_interview
        if total_w == 0:
            total_w = 1.0
        w_cv /= total_w
        w_assessment /= total_w
        w_interview /= total_w

        logger.info(
            "[final_ranker] JR %d — weights: CV=%.2f Assessment=%.2f Interview=%.2f",
            requisition_id, w_cv, w_assessment, w_interview,
        )

        # ── Build per-application score records ───────────────────────────────
        scored: list[dict] = []

        for app in applications:
            app_id = app.application_id

            # CV semantic score (0-100)
            sem_report: SemanticAnalysisReport = (
                db.query(SemanticAnalysisReport)
                .filter(SemanticAnalysisReport.application_id == app_id)
                .first()
            )
            cv_score = float(sem_report.match_percentage) if sem_report else 0.0

            # Assessment score from leaderboard (0-1 → 0-100)
            lb_entry: AssessmentLeaderboard = (
                db.query(AssessmentLeaderboard)
                .filter(
                    AssessmentLeaderboard.application_id == app_id,
                    AssessmentLeaderboard.jr_id == requisition_id,
                )
                .first()
            )
            assessment_score = (
                float(lb_entry.final_score) * 100.0
                if lb_entry and lb_entry.final_score is not None
                else None
            )

            # Technical interview score (already 0-100 or Decimal)
            interview_session: TechnicalInterviewSession = (
                db.query(TechnicalInterviewSession)
                .filter(
                    TechnicalInterviewSession.application_id == app_id,
                    TechnicalInterviewSession.status == "Completed",
                )
                .first()
            )
            interview_score = (
                float(interview_session.overall_score)
                if interview_session and interview_session.overall_score is not None
                else None
            )

            # Weighted total
            wtotal = w_cv * cv_score
            if has_assessment:
                wtotal += w_assessment * (assessment_score or 0.0)
            if has_interview:
                wtotal += w_interview * (interview_score or 0.0)

            # Red-flag detection
            red_flag = False
            red_flag_reason = None
            if (
                cv_score >= _RED_FLAG_CV_MIN
                and interview_score is not None
                and interview_score < _RED_FLAG_INTERVIEW_MAX
            ):
                red_flag = True
                red_flag_reason = (
                    f"CV score {cv_score:.1f}% ranks candidate highly, but "
                    f"live interview score {interview_score:.1f}% is below threshold "
                    f"({_RED_FLAG_INTERVIEW_MAX}%). Manual review required."
                )
                logger.warning(
                    "[final_ranker] RED FLAG app_id=%d (JR %d): CV=%.1f%% interview=%.1f%%",
                    app_id, requisition_id, cv_score, interview_score,
                )

            scored.append({
                "application_id": app_id,
                "posting_id": posting.posting_id,
                "semantic_score": round(cv_score, 2),
                "assessment_score": round(assessment_score, 2) if assessment_score is not None else None,
                "technical_interview_score": round(interview_score, 2) if interview_score is not None else None,
                "hr_interview_score": None,
                "weighted_total_score": round(wtotal, 2),
                "red_flag": red_flag,
                "red_flag_reason": red_flag_reason,
                "candidate": app.candidate,
            })

        # ── Sort and assign ranks ─────────────────────────────────────────────
        # Red-flagged candidates are sorted to the bottom of their natural rank
        scored.sort(key=lambda x: (x["red_flag"], -x["weighted_total_score"]))

        # ── Upsert FinalRanking rows ──────────────────────────────────────────
        _TOP_HIRE_COUNT = 5  # top N who receive "hire" decision email

        for rank, row in enumerate(scored, start=1):
            existing_fr: FinalRanking = (
                db.query(FinalRanking)
                .filter(FinalRanking.application_id == row["application_id"])
                .first()
            )
            if existing_fr:
                existing_fr.semantic_score = row["semantic_score"]
                existing_fr.assessment_score = row["assessment_score"]
                existing_fr.technical_interview_score = row["technical_interview_score"]
                existing_fr.hr_interview_score = row["hr_interview_score"]
                existing_fr.weighted_total_score = row["weighted_total_score"]
                existing_fr.final_rank = rank
                existing_fr.final_recommendation = "Hire" if rank <= _TOP_HIRE_COUNT else "No Hire"
                existing_fr.final_status = "Selected" if rank <= _TOP_HIRE_COUNT else "Not Selected"
                existing_fr.red_flag = row["red_flag"]
                existing_fr.red_flag_reason = row["red_flag_reason"]
            else:
                db.add(FinalRanking(
                    application_id=row["application_id"],
                    posting_id=row["posting_id"],
                    semantic_score=row["semantic_score"],
                    assessment_score=row["assessment_score"],
                    technical_interview_score=row["technical_interview_score"],
                    hr_interview_score=row["hr_interview_score"],
                    weighted_total_score=row["weighted_total_score"],
                    final_rank=rank,
                    final_recommendation="Hire" if rank <= _TOP_HIRE_COUNT else "No Hire",
                    final_status="Selected" if rank <= _TOP_HIRE_COUNT else "Not Selected",
                    red_flag=row["red_flag"],
                    red_flag_reason=row["red_flag_reason"],
                ))
            row["_rank"] = rank

        jr.status = "ranking_complete"
        db.commit()

        logger.info(
            "[final_ranker] Requisition %d — %d candidates ranked. Top: %s @ %.2f",
            requisition_id,
            len(scored),
            scored[0]["candidate"].first_name if scored else "—",
            scored[0]["weighted_total_score"] if scored else 0,
        )

    except Exception as exc:
        db.rollback()
        logger.exception(
            "[final_ranker] DB error for requisition %d — retrying.", requisition_id
        )
        raise self.retry(exc=exc, countdown=120 * (self.request.retries + 1))
    finally:
        db.close()

    # ── Send hire/no-hire decision emails ─────────────────────────────────────
    email_sent = 0
    for row in scored:
        candidate: Candidate = row["candidate"]
        if not candidate or not candidate.email:
            continue
        rank = row.get("_rank", 999)
        decision = "hire" if rank <= _TOP_HIRE_COUNT else "reject"
        success = send_final_decision_sync(
            recipient_email=candidate.email,
            first_name=candidate.first_name or "Candidate",
            job_title=job_title,
            company_name=company_name,
            decision=decision,
            final_rank=rank if decision == "hire" else None,
        )
        if success:
            email_sent += 1
        time.sleep(0.2)  # light throttle — avoid SMTP burst

    logger.info(
        "[final_ranker] Requisition %d — %d decision emails sent.",
        requisition_id, email_sent,
    )

    red_flags = [r for r in scored if r["red_flag"]]
    return {
        "requisition_id": requisition_id,
        "ranked": len(scored),
        "red_flags": len(red_flags),
        "emails_sent": email_sent,
        "top_candidate": (
            f"{scored[0]['candidate'].first_name} {scored[0]['candidate'].last_name}"
            if scored else None
        ),
        "top_score": scored[0]["weighted_total_score"] if scored else None,
    }

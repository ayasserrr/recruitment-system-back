"""
Celery application factory.

Beat schedule
─────────────
• scan_and_dispatch_scheduled_posts — runs every 60 s
  Scans job_requisitions for rows where:
    status == 'Active'  AND  posting_start_date <= today
  Then fires one process_linkedin_publishing task per match.

• scan_and_dispatch_cv_ranking — runs every 5 minutes
  Scans job_requisitions for rows where:
    cv_collection_end_date <= today  AND  status IN ('published','Active')
  Then fires one process_cv_ranking task per match.
  After a successful rank run the worker sets status = 'ranked' so this
  scanner never re-triggers the same job.

Workers
───────
• process_linkedin_publishing(jr_id, company_id)
  Executes the LangGraph LinkedIn publishing workflow.

• process_cv_ranking(requisition_id)
  Executes the LangGraph AI ranking pipeline (GPT-4o-mini, pool-relative
  labels, upserts semantic_analysis_reports + SemanticMatchedSkill).

Run commands
────────────
  # Worker  (handles both task modules)
  celery -A core.celery worker --loglevel=info

  # Beat scheduler  (separate process)
  celery -A core.celery beat  --loglevel=info
"""

import os
from celery import Celery
from celery.signals import worker_init

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@worker_init.connect
def _log_gpu_on_start(**_kwargs) -> None:
    from core.gpu import log_device_info
    log_device_info()

celery_app = Celery(
    "recruitment_system",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.social_tasks",
        "tasks.ranking_tasks",    # ← CV ranking automation
        "tasks.assessment_tasks", # ← Technical assessment generation
        "tasks.interview_tasks",  # ← Interview scheduling + final ranking
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Prevent tasks from running twice if a worker crashes mid-execution
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# ── Periodic tasks (Celery Beat) ──────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # LinkedIn post publishing — tight cadence is fine (fast, low CPU, no LLM)
    "scan-scheduled-posts-every-minute": {
        "task": "tasks.social_tasks.scan_and_dispatch_scheduled_posts",
        "schedule": 60.0,
    },
    # CV ranking — 5-minute cadence.  A full ranking run takes 3-10 minutes;
    # firing every 60 s would dispatch 5+ duplicates before the first finishes.
    # The processing_status lock prevents work duplication, but the extra
    # dispatches still consume Redis queue space and worker slots.
    "scan-cv-ranking-deadlines-every-5-minutes": {
        "task": "tasks.ranking_tasks.scan_and_dispatch_cv_ranking",
        "schedule": 300.0,
    },
    # Assessment generation — 2-minute cadence.  The pipeline is fast (< 2 min)
    # but status-based guard + processing lock prevent duplicates regardless.
    "scan-assessment-generation-every-2-minutes": {
        "task": "tasks.assessment_tasks.scan_and_dispatch_assessment",
        "schedule": 120.0,
    },
    # Expire stale/abandoned assessments — 30-minute cadence is fine
    "expire-stale-assessments-every-30-minutes": {
        "task": "tasks.assessment_tasks.scan_and_expire_assessments",
        "schedule": 1800.0,
    },
    # Post-deadline pool ranking — 5-minute cadence (pool ranking + GPT report)
    "scan-assessment-ranking-every-5-minutes": {
        "task": "tasks.assessment_tasks.scan_and_dispatch_assessment_ranking",
        "schedule": 300.0,
    },
    # Final ranking after interview deadline — 5-minute cadence
    "scan-final-ranking-every-5-minutes": {
        "task": "tasks.interview_tasks.scan_and_dispatch_final_ranking",
        "schedule": 300.0,
    },
}

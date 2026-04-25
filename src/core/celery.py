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
    cv_collection_end_date <= today  AND  status IN ('published','Active','failed')
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

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
    # LinkedIn post publishing — checked every minute (posting windows matter)
    "scan-scheduled-posts-every-minute": {
        "task": "tasks.social_tasks.scan_and_dispatch_scheduled_posts",
        "schedule": 60.0,
    },
    # CV ranking — checked every minute
    "scan-cv-ranking-deadlines-every-minute": {
        "task": "tasks.ranking_tasks.scan_and_dispatch_cv_ranking",
        "schedule": 60.0,
    },
    # Assessment generation — checked every minute after ranking completes
    "scan-assessment-generation-every-minute": {
        "task": "tasks.assessment_tasks.scan_and_dispatch_assessment",
        "schedule": 60.0,
    },
    # Expire stale/abandoned assessments — checked every 30 minutes
    "expire-stale-assessments-every-30-minutes": {
        "task": "tasks.assessment_tasks.scan_and_expire_assessments",
        "schedule": 1800.0,
    },
    # Post-deadline pool ranking + no-show marking — checked every minute
    "scan-assessment-ranking-every-minute": {
        "task": "tasks.assessment_tasks.scan_and_dispatch_assessment_ranking",
        "schedule": 60.0,
    },
    # Final ranking after interview deadline — checked every 5 minutes
    "scan-final-ranking-every-5-minutes": {
        "task": "tasks.interview_tasks.scan_and_dispatch_final_ranking",
        "schedule": 300.0,
    },
}

"""
Celery application factory.

Beat schedule
─────────────
• scan_and_dispatch_scheduled_posts — runs every 60 s
  Scans job_requisitions for rows where:
    status == 'scheduled'  AND  posting_start_date <= today
  Then fires one process_linkedin_publishing task per match.

Workers
───────
• process_linkedin_publishing(jr_id, company_id)
  Executes the LangGraph publishing workflow for a single job requisition.

Run commands
────────────
  # Worker
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
    include=["tasks.social_tasks"],
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
    "scan-scheduled-posts-every-minute": {
        "task": "tasks.social_tasks.scan_and_dispatch_scheduled_posts",
        "schedule": 60.0,  # seconds
    },
}

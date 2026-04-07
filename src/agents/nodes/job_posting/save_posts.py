import logging
from datetime import datetime
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from models.db.job_requisition import JobRequisition
from models.db.posting_platform import PostingPlatform
from models.db.job_posting import JobPosting
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def save_posts(state: PipelineState) -> PipelineState:
    """
    Phase 1 — Node 3
    Persist generated posts to the DB and advance the pipeline:
      • posting_platforms  → title, content, ai_generated flags
      • job_postings       → create row, state = "Open"
      • job_requisitions   → status = "Active"
    Advances current_phase to "cv_screening" when done.
    """
    db: Session = SessionLocal()
    try:
        rid   = state["requisition_id"]
        posts = state["generated_posts"]
        now   = datetime.utcnow()

        # ── posting_platforms ──────────────────────────────────────────────
        platforms = db.query(PostingPlatform).filter(
            PostingPlatform.requisition_id == rid
        ).all()

        for platform in platforms:
            key       = platform.platform_name.lower()
            post_data = posts.get(key) or posts.get("linkedin") or list(posts.values())[0]

            platform.platform_post_title   = post_data["title"]
            platform.platform_post_content = post_data["content"]
            platform.ai_generated          = True
            platform.ai_model_version      = "gpt-4o-mini"
            platform.generated_at          = now
            platform.status                = "Generated"

        # ── job_postings ───────────────────────────────────────────────────
        job_posting = db.query(JobPosting).filter(
            JobPosting.requisition_id == rid
        ).first()

        if job_posting:
            job_posting.state       = "Open"
            job_posting.posted_date = now
        else:
            job_posting = JobPosting(
                requisition_id=rid,
                state="Open",
                posted_date=now,
            )
            db.add(job_posting)
            db.flush()   # populate posting_id

        posting_id = job_posting.posting_id

        # ── requisition status ─────────────────────────────────────────────
        jr = db.query(JobRequisition).filter(
            JobRequisition.requisition_id == rid
        ).first()
        if jr:
            jr.status = "Active"

        db.commit()
        logger.info(
            f"[save_posts] Phase 1 complete for requisition {rid}. "
            f"Advancing to cv_screening."
        )
        return {
            **state,
            "posting_id":    posting_id,
            "current_phase": "cv_screening",   # ← hand off to next phase
        }

    except Exception as e:
        db.rollback()
        logger.error(f"[save_posts] Error: {e}")
        return {**state, "current_phase": "error", "error": str(e)}
    finally:
        db.close()

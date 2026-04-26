"""
Node 1 — gather_applications_node

Loads all Shortlisted applications for the JobRequisition and resolves the
posting_id / company metadata needed by downstream nodes.
"""

import logging

from database.connection import SessionLocal
from graphs.states.final_ranking_state import FinalRankingState
from models.db.application import Application
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport

logger = logging.getLogger(__name__)


def gather_applications_node(state: FinalRankingState) -> FinalRankingState:
    """Load all applications that should participate in the final ranking."""
    if state.get("error"):
        return state

    requisition_id = state["requisition_id"]
    db = SessionLocal()
    try:
        jr: JobRequisition = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        if not jr:
            return {**state, "error": f"JobRequisition {requisition_id} not found."}

        posting: JobPosting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return {**state, "error": f"No JobPosting found for requisition {requisition_id}."}

        company_name = jr.company.name if jr.company else "Our Company"

        # Primary: Shortlisted applications
        applications: list[Application] = (
            db.query(Application)
            .filter(
                Application.posting_id == posting.posting_id,
                Application.status.in_(["Shortlisted", "interview_pending"]),
            )
            .all()
        )

        # Fallback: any application that has a semantic report
        if not applications:
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
            return {
                **state,
                "error": f"No applications found for requisition {requisition_id}.",
            }

        app_records = [
            {
                "application_id": app.application_id,
                "posting_id": posting.posting_id,
                "candidate": app.candidate,
            }
            for app in applications
        ]

        logger.info(
            "[final_ranking:gather] JR %d — %d applications loaded.",
            requisition_id,
            len(app_records),
        )
        return {
            **state,
            "posting_id": posting.posting_id,
            "job_title": jr.job_title,
            "company_name": company_name,
            "applications": app_records,
        }

    except Exception as exc:
        logger.exception(
            "[final_ranking:gather] Error loading applications for JR %d.", requisition_id
        )
        return {**state, "error": str(exc)}
    finally:
        db.close()

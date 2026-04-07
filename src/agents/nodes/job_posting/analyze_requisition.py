import logging
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from models.db.job_requisition import JobRequisition
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def analyze_requisition(state: PipelineState) -> PipelineState:
    """
    Phase 1 — Node 1
    Fetch the Job Requisition from DB and build the jr_data dict
    that the LLM nodes will use to generate posts.
    """
    db: Session = SessionLocal()
    try:
        rid = state["requisition_id"]

        jr = db.query(JobRequisition).filter(
            JobRequisition.requisition_id == rid
        ).first()

        if not jr:
            return {**state, "current_phase": "error", "error": f"Requisition {rid} not found."}

        required_skills  = [s.skill_name for s in jr.required_skills if s.skill_type == "required"]
        preferred_skills = [s.skill_name for s in jr.required_skills if s.skill_type == "preferred"]
        languages = [
            f"{l.language} ({l.proficiency_level})" if l.proficiency_level else l.language
            for l in jr.languages
        ]
        platforms = [p.platform_name for p in jr.posting_platforms]

        jr_data = {
            "job_title":            jr.job_title,
            "department":           jr.department,
            "seniority_level":      jr.seniority_level,
            "employment_type":      jr.employment_type,
            "location":             f"{jr.location_city or ''}, {jr.location_country or ''}".strip(", "),
            "remote_available":     jr.remote_available,
            "required_skills":      required_skills,
            "preferred_skills":     preferred_skills,
            "min_experience":       jr.min_years_experience,
            "max_experience":       jr.max_years_experience,
            "min_education":        jr.min_education_level,
            "key_responsibilities": jr.key_responsibilities,
            "full_description":     jr.full_job_description,
            "currency":             jr.currency,
            "min_salary":           float(jr.min_salary_monthly) if jr.min_salary_monthly else None,
            "max_salary":           float(jr.max_salary_monthly) if jr.max_salary_monthly else None,
            "deadline":             str(jr.application_deadline) if jr.application_deadline else None,
            "languages":            languages,
            "platforms":            platforms,
        }

        logger.info(f"[analyze_requisition] Requisition {rid} loaded: '{jr.job_title}'")
        return {**state, "jr_data": jr_data}

    except Exception as e:
        logger.error(f"[analyze_requisition] Error: {e}")
        return {**state, "current_phase": "error", "error": str(e)}
    finally:
        db.close()

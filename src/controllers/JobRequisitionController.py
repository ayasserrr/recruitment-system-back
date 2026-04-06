import re
from sqlalchemy.orm import Session

from models.schemas.job_schema import JobRequisitionCreate
from models.db.job_requisition import JobRequisition
from models.db.requisition_required_skill import RequisitionRequiredSkill
from models.db.requisition_language import RequisitionLanguage
from models.db.posting_platform import PostingPlatform
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from models.db.technical_interview_config import TechnicalInterviewConfig
from models.db.interview_evaluation_criterion import InterviewEvaluationCriterion
from models.db.hr_interview_config import HRInterviewConfig
from models.db.hr_interview_criterion import HRInterviewCriterion


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_language(raw: str) -> tuple[str, str | None]:
    """
    Split "English (Fluent)" → ("English", "Fluent").
    If no parentheses, proficiency_level is None.
    """
    match = re.match(r"^(.+?)\s*\((.+?)\)\s*$", raw.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw.strip(), None


def _safe_int(value: str | None) -> int | None:
    """Convert a string like '45' or '1' to int; return None if blank/None."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _even_weight(count: int) -> int | None:
    return (100 // count) if count > 0 else None


def _clean(lst: list) -> list:
    """Remove None and empty-string values from a list."""
    return [item for item in lst if item is not None and str(item).strip() != ""]


# ── main function ──────────────────────────────────────────────────────────

def get_requisition_for_recruiter(
    requisition_id: int,
    recruiter_id: int,
    db: Session,
) -> JobRequisition:
    """
    Fetch a requisition only if it belongs to the requesting recruiter.
    Raises 404 if not found, 403 if owned by a different recruiter.
    """
    requisition = db.query(JobRequisition).filter(
        JobRequisition.requisition_id == requisition_id
    ).first()

    if not requisition:
        raise ValueError(f"Requisition {requisition_id} not found.")

    if requisition.recruiter_id != recruiter_id:
        raise PermissionError("You do not have permission to access this requisition.")

    return requisition


def update_requisition_status(
    requisition_id: int,
    recruiter_id: int,
    new_status: str,
    db: Session,
) -> JobRequisition:
    """
    Update the status of a requisition — only if owned by the recruiter.
    """
    requisition = get_requisition_for_recruiter(requisition_id, recruiter_id, db)
    requisition.status = new_status
    db.commit()
    db.refresh(requisition)
    return requisition


def create_full_requisition(
    data: JobRequisitionCreate,
    recruiter_id: int,
    company_id: int,
    db: Session,
) -> JobRequisition:
    """
    Atomically writes a full job requisition across 9 tables.
    Rolls back entirely on any failure.
    """
    try:
        # ── 1. job_requisitions ────────────────────────────────────────────
        responsibilities_text = "\n".join(_clean(data.responsibilities)) or None

        requisition = JobRequisition(
            company_id=company_id,
            recruiter_id=recruiter_id,
            # basic info
            job_title=data.jobTitle,
            department=data.department,
            seniority_level=data.seniorityLevel,
            employment_type=data.employmentType,
            location_city=data.city,
            location_country=data.country,
            remote_available=data.remoteAvailable,
            # requirements
            min_years_experience=data.minExperience,
            max_years_experience=data.maxExperience,
            min_education_level=data.minEducation,
            field_of_study=data.fieldOfStudy,
            # description
            key_responsibilities=responsibilities_text,
            full_job_description=data.fullDescription,
            # compensation
            currency=data.currency,
            min_salary_monthly=data.minSalary,
            max_salary_monthly=data.maxSalary,
            application_deadline=data.deadline,
            contact_email=data.contactEmail,
            additional_notes=data.additionalNotes,
            # posting dates
            posting_start_date=data.postingStartDate,
            cv_collection_end_date=data.postingEndDate,
            status="Draft",
        )
        db.add(requisition)
        db.flush()  # populate requisition_id

        # ── 2. requisition_required_skills ────────────────────────────────
        for skill in _clean(data.requiredSkills):
            db.add(RequisitionRequiredSkill(
                requisition_id=requisition.requisition_id,
                skill_name=skill,
                skill_type="required",
            ))
        for skill in _clean(data.preferredSkills):
            db.add(RequisitionRequiredSkill(
                requisition_id=requisition.requisition_id,
                skill_name=skill,
                skill_type="preferred",
            ))

        # ── 3. requisition_languages ──────────────────────────────────────
        for raw_lang in _clean(data.languages):
            lang, proficiency = _parse_language(raw_lang)
            db.add(RequisitionLanguage(
                requisition_id=requisition.requisition_id,
                language=lang,
                proficiency_level=proficiency,
            ))

        # ── 4. posting_platforms ──────────────────────────────────────────
        for platform_name in _clean(data.selectedPlatforms):
            db.add(PostingPlatform(
                requisition_id=requisition.requisition_id,
                platform_name=platform_name,
                status="Pending",
            ))

        # ── 5. technical_assessment_configs ───────────────────────────────
        # For "ai-generated": sample_task stays NULL (auto-generated later).
        # For "custom": use templateTask; fall back to serialised question list.
        is_ai_generated = (data.assessmentType or "").lower() == "ai-generated"
        clean_assessment_questions = _clean(data.assessmentQuestions)
        if is_ai_generated:
            sample_task = None
        else:
            sample_task = data.templateTask or ("\n".join(clean_assessment_questions) or None)

        db.add(TechnicalAssessmentConfig(
            requisition_id=requisition.requisition_id,
            assessment_type=data.assessmentType,
            time_limit_minutes=data.assessmentTimeLimit,
            candidates_to_advance=data.assessmentCandidatesToAdvance,
            max_attempts=_safe_int(data.maxAttempts) or 1,
            assessment_language=data.assessmentLanguage,
            sample_task=sample_task,
            assessment_questions="\n".join(clean_assessment_questions) or None,
        ))

        # ── 6. technical_interview_configs + interview_evaluation_criteria ─
        tech_config = TechnicalInterviewConfig(
            requisition_id=requisition.requisition_id,
            interview_type=data.technicalInterviewType,
            duration_minutes=_safe_int(data.technicalInterviewDuration),
            ai_feedback_level=data.aiFeedbackLevel,
            scoring_system=data.scoringSystem,
            candidates_to_advance=data.technicalInterviewCandidatesToAdvance,
            number_of_interviewers=_safe_int(data.numberOfInterviewers),
            interviewer_notes=data.interviewerNotes,
            interview_questions="\n".join(_clean(data.technicalInterviewQuestions)) or None,
        )
        db.add(tech_config)
        db.flush()  # populate config_id

        clean_tech_criteria = _clean(data.aiEvaluationCriteria)
        weight = _even_weight(len(clean_tech_criteria))
        for criterion_name in clean_tech_criteria:
            db.add(InterviewEvaluationCriterion(
                config_id=tech_config.config_id,
                criterion_name=criterion_name,
                max_score=10,
                weight_percentage=weight,
            ))

        # ── 7. hr_interview_configs + hr_interview_criteria ───────────────
        hr_config = HRInterviewConfig(
            requisition_id=requisition.requisition_id,
            interview_type=data.hrInterviewType,
            interview_format=data.hrInterviewFormat,
            duration_minutes=_safe_int(data.hrInterviewDuration),
            candidates_to_advance=data.hrInterviewCandidatesToAdvance,
            interview_questions="\n".join(_clean(data.hrInterviewQuestions)) or None,
            culture_values=data.cultureValues,
            decision_timeline=data.hrDecisionTimeline,
            interviewer_notes=data.hrInterviewerNotes,
        )
        db.add(hr_config)
        db.flush()  # populate config_id

        clean_hr_criteria = _clean(data.hrEvaluationCriteria)
        hr_weight = _even_weight(len(clean_hr_criteria))
        for criterion_name in clean_hr_criteria:
            db.add(HRInterviewCriterion(
                config_id=hr_config.config_id,
                criterion_name=criterion_name,
                max_score=10,
                weight_percentage=hr_weight,
            ))

        # ── commit everything atomically ──────────────────────────────────
        db.commit()
        db.refresh(requisition)
        return requisition

    except Exception:
        db.rollback()
        raise

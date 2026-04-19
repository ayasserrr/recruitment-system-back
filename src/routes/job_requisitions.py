from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database.connection import get_db
from models.db.application import Application
from models.db.assessment_report import AssessmentReport
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.semantic_matched_skill import SemanticMatchedSkill
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from models.schemas.job_schema import JobRequisitionCreate, JobRequisitionResponse
from controllers.JobRequisitionController import (
    create_full_requisition,
    get_requisition_for_recruiter,
    update_requisition_status,
)
from helpers.auth_helper import verify_token

router = APIRouter(prefix="/api/v1/requisitions", tags=["job-requisitions"])
security = HTTPBearer()


# ── Auth dependency ────────────────────────────────────────────────────────

def _get_recruiter_context(token: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Accepts both token types:
      - Recruiter token  → has recruiter_id + company_id
      - Company token    → has company_id only (recruiter_id will be None)
    Raises 401 only if the token is invalid or has neither ID.
    """
    try:
        payload = verify_token(token.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired. Please log in again.",
        )

    recruiter_id = payload.get("recruiter_id")   # None for company tokens
    company_id   = payload.get("company_id")

    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid. Please log in again.",
        )

    return {"recruiter_id": recruiter_id, "company_id": company_id}


# ── POST /full ─────────────────────────────────────────────────────────────

@router.post(
    "/full",
    response_model=JobRequisitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a full job requisition",
)
def create_full_requisition_endpoint(
    data: JobRequisitionCreate,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    try:
        requisition = create_full_requisition(
            data=data,
            recruiter_id=ctx["recruiter_id"],
            company_id=ctx["company_id"],
            db=db,
        )
        return requisition

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A requisition with this configuration already exists.",
        )
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is currently unavailable. Please try again later.",
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        )


# ── GET /{id} ──────────────────────────────────────────────────────────────

@router.get(
    "/{requisition_id}",
    response_model=JobRequisitionResponse,
    summary="Get a job requisition (owner only)",
)
def get_requisition(
    requisition_id: int,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    try:
        return get_requisition_for_recruiter(requisition_id, ctx["recruiter_id"], db)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this requisition.",
        )


# ── PATCH /{id}/status ─────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str


# ── POST /{id}/retry ───────────────────────────────────────────────────────

@router.post(
    "/{requisition_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-trigger the recruitment pipeline for a requisition",
)
def retry_pipeline(
    requisition_id: int,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    from agents.runner import trigger_pipeline

    try:
        req = get_requisition_for_recruiter(requisition_id, ctx["recruiter_id"], db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this requisition.",
        )

    trigger_pipeline(req.requisition_id)
    return {"detail": f"Pipeline re-triggered for requisition {requisition_id}."}


@router.patch(
    "/{requisition_id}/status",
    response_model=JobRequisitionResponse,
    summary="Update requisition status (owner only)",
)
def update_status(
    requisition_id: int,
    body: StatusUpdate,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    try:
        return update_requisition_status(
            requisition_id=requisition_id,
            recruiter_id=ctx["recruiter_id"],
            new_status=body.status,
            db=db,
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this requisition.",
        )


# ── GET /{jr_id}/semantic-analysis ────────────────────────────────────────

def _require_requisition(jr_id: int, company_id: int, db: Session) -> JobRequisition:
    req = (
        db.query(JobRequisition)
        .filter(
            JobRequisition.requisition_id == jr_id,
            JobRequisition.company_id == company_id,
        )
        .first()
    )
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requisition not found.")
    return req


@router.get("/{jr_id}/semantic-analysis", summary="Semantic analysis results for a requisition")
def get_semantic_analysis(
    jr_id: int,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    _require_requisition(jr_id, ctx["company_id"], db)

    rows = (
        db.query(
            Candidate.first_name,
            Candidate.last_name,
            SemanticAnalysisReport.report_id,
            SemanticAnalysisReport.match_percentage,
            SemanticAnalysisReport.recommendation_summary,
        )
        .join(Application, Application.candidate_id == Candidate.candidate_id)
        .join(JobPosting, JobPosting.posting_id == Application.posting_id)
        .join(SemanticAnalysisReport, SemanticAnalysisReport.application_id == Application.application_id)
        .filter(JobPosting.requisition_id == jr_id)
        .order_by(SemanticAnalysisReport.match_percentage.desc())
        .all()
    )

    if not rows:
        return []

    report_ids = [r.report_id for r in rows]
    skills = (
        db.query(SemanticMatchedSkill)
        .filter(SemanticMatchedSkill.report_id.in_(report_ids))
        .all()
    )
    skills_map: dict = defaultdict(lambda: {"matched": [], "missing": []})
    for skill in skills:
        bucket = "missing" if skill.match_type == "missing" else "matched"
        skills_map[skill.report_id][bucket].append(skill.skill_name)

    return [
        {
            "candidate_name": f"{r.first_name} {r.last_name}",
            "match_score": round(float(r.match_percentage), 2) if r.match_percentage is not None else None,
            "technical_score": None,
            "experience_score": None,
            "reasoning_summary": r.recommendation_summary,
            "top_matched_skills": skills_map[r.report_id]["matched"],
            "missing_skills": skills_map[r.report_id]["missing"],
        }
        for r in rows
    ]


# ── GET /{jr_id}/assessment-analytics ─────────────────────────────────────

@router.get("/{jr_id}/assessment-analytics", summary="Technical assessment analytics for a requisition")
def get_assessment_analytics(
    jr_id: int,
    ctx: dict = Depends(_get_recruiter_context),
    db: Session = Depends(get_db),
):
    _require_requisition(jr_id, ctx["company_id"], db)

    config = (
        db.query(TechnicalAssessmentConfig)
        .filter(TechnicalAssessmentConfig.requisition_id == jr_id)
        .first()
    )
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment configured for this requisition.",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    is_closed = config.assessment_deadline is not None and config.assessment_deadline <= now

    total_invited: int = (
        db.query(func.count(CandidateAssessment.assessment_id))
        .filter(CandidateAssessment.config_id == config.config_id)
        .scalar()
    ) or 0

    total_submitted: int = (
        db.query(func.count(CandidateAssessment.assessment_id))
        .filter(
            CandidateAssessment.config_id == config.config_id,
            CandidateAssessment.status == "Submitted",
        )
        .scalar()
    ) or 0

    candidate_rows = (
        db.query(
            Candidate.first_name,
            Candidate.last_name,
            CandidateAssessment.total_score,
            CandidateAssessment.status,
            AssessmentReport.rank_in_pool,
            AssessmentReport.ai_feedback,
        )
        .join(Application, Application.application_id == CandidateAssessment.application_id)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .outerjoin(AssessmentReport, AssessmentReport.assessment_id == CandidateAssessment.assessment_id)
        .filter(CandidateAssessment.config_id == config.config_id)
        .order_by(AssessmentReport.rank_in_pool.asc())
        .all()
    )

    return {
        "metadata": {
            "pool_report": config.pool_report,
            "deadline": config.assessment_deadline.isoformat() if config.assessment_deadline else None,
            "status": "Closed" if is_closed else "Open",
            "total_invited": total_invited,
            "total_submitted": total_submitted,
        },
        "candidates": [
            {
                "candidate_name": f"{r.first_name} {r.last_name}",
                "total_score": round(float(r.total_score), 2) if r.total_score is not None else None,
                "rank_in_pool": r.rank_in_pool,
                "status": "Submitted" if r.status == "Submitted" else "No-show",
                "ai_feedback": r.ai_feedback,
            }
            for r in candidate_rows
        ],
    }

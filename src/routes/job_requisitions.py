from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError
from jose import JWTError
from pydantic import BaseModel

from database.connection import get_db
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

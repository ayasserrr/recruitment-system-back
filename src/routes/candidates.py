from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional

from database.connection import get_db
from models.db.candidate import Candidate
from routes.jobs import check_job_deadline

candidates_router = APIRouter(
    prefix="/api/v1/candidates",
    tags=["candidates"],
)


class CandidateRegisterRequest(BaseModel):
    # Accept both snake_case and camelCase from the frontend
    first_name: str = Field(default="", alias="firstName")
    last_name: str = Field(default="", alias="lastName")
    email: str
    requisition_id: Optional[int] = Field(default=None, alias="requisitionId")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, values: dict) -> dict:
        # Accept snake_case in addition to camelCase
        if not values.get("firstName") and values.get("first_name"):
            values["firstName"] = values["first_name"]
        if not values.get("lastName") and values.get("last_name"):
            values["lastName"] = values["last_name"]
        # Treat missing / null / whitespace-only names as "Unknown"
        if not (values.get("firstName") or "").strip():
            values["firstName"] = "Unknown"
        if not (values.get("lastName") or "").strip():
            values["lastName"] = "Unknown"
        return values


@candidates_router.post("", status_code=status.HTTP_201_CREATED)
@candidates_router.post("/register", status_code=status.HTTP_201_CREATED)
def register_candidate(body: CandidateRegisterRequest, db: Session = Depends(get_db)):
    """
    Register a new candidate (Name + Email) and return the candidate_id.

    Optionally accepts requisitionId — if provided, validates that the job is
    still accepting applications before creating the record.

    The candidate_id is required for the subsequent CV upload step:
      POST /api/v1/data/upload/{company_id}/{job_id}/{candidate_id}
    """
    # Deadline guard — runs before any DB write
    if body.requisition_id is not None:
        check_job_deadline(body.requisition_id, db)
    try:
        candidate = Candidate(
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "candidate_id": candidate.candidate_id,
                "first_name": candidate.first_name,
                "last_name": candidate.last_name,
                "email": candidate.email,
            },
        )
    except IntegrityError:
        db.rollback()
        # Email already exists — return the existing candidate's ID
        existing = (
            db.query(Candidate)
            .filter(Candidate.email == body.email)
            .first()
        )
        if existing:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "candidate_id": existing.candidate_id,
                    "first_name": existing.first_name,
                    "last_name": existing.last_name,
                    "email": existing.email,
                    "note": "Candidate with this email already exists.",
                },
            )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "A candidate with this email already exists."},
        )

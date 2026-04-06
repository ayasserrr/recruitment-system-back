from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from datetime import datetime

from database.connection import get_db
from models.db.recruiter import Recruiter
from models.db.company import Company
from models.schemas.recruiter_schema import (
    RecruiterSignupRequest,
    RecruiterLoginRequest,
    RecruiterAuthResponse,
    RecruiterResponse,
)
from helpers.auth_helper import (
    get_password_hash,
    verify_password,
    create_recruiter_token,
    verify_token,
)

router = APIRouter(prefix="/api/v1/recruiters", tags=["recruiter-auth"])
security = HTTPBearer()


@router.post("/signup", response_model=RecruiterAuthResponse, status_code=status.HTTP_201_CREATED)
def recruiter_signup(data: RecruiterSignupRequest, db: Session = Depends(get_db)):
    # Verify company exists
    company = db.query(Company).filter(Company.company_id == data.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    # Check email uniqueness
    if db.query(Recruiter).filter(Recruiter.email == data.email).first():
        raise HTTPException(status_code=409, detail="A recruiter with this email already exists.")

    recruiter = Recruiter(
        company_id=data.company_id,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        password_hash=get_password_hash(data.password),
        role=data.role,
        phone=data.phone,
        bio=data.bio,
        profile_picture=data.profile_picture,
    )
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)

    token = create_recruiter_token(recruiter.recruiter_id, recruiter.company_id, recruiter.email)
    return RecruiterAuthResponse(
        access_token=token,
        recruiter=RecruiterResponse.model_validate(recruiter),
    )


@router.post("/login", response_model=RecruiterAuthResponse)
def recruiter_login(data: RecruiterLoginRequest, db: Session = Depends(get_db)):
    recruiter = db.query(Recruiter).filter(Recruiter.email == data.email).first()

    if not recruiter or not verify_password(data.password, recruiter.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    recruiter.last_login = datetime.utcnow()
    db.commit()

    token = create_recruiter_token(recruiter.recruiter_id, recruiter.company_id, recruiter.email)
    return RecruiterAuthResponse(
        access_token=token,
        recruiter=RecruiterResponse.model_validate(recruiter),
    )


@router.get("/me", response_model=RecruiterResponse)
def get_current_recruiter(
    token: str = Depends(security),
    db: Session = Depends(get_db),
):
    payload = verify_token(token.credentials)
    recruiter_id = payload.get("recruiter_id")
    if not recruiter_id:
        raise HTTPException(status_code=401, detail="Invalid token.")

    recruiter = db.query(Recruiter).filter(Recruiter.recruiter_id == recruiter_id).first()
    if not recruiter:
        raise HTTPException(status_code=404, detail="Recruiter not found.")

    return RecruiterResponse.model_validate(recruiter)

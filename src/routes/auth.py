from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from typing import Optional
from datetime import datetime

from database.connection import get_db
from models.db.company import Company
from models.schemas.auth import SignupRequest, LoginRequest, AuthResponse, CompanyResponse
from helpers.auth_helper import (
    get_password_hash, 
    verify_password, 
    create_company_token,
    authenticate_company
)
from enums.auth_errors import (
    AuthErrorMessages, 
    HTTPStatusCodes, 
    AuthErrorDetails
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
security = HTTPBearer()

@router.post("/signup", response_model=AuthResponse)
async def signup(signup_request: SignupRequest, db: Session = Depends(get_db)):
    """
    Signup endpoint for new company accounts.
    Creates a new company account with unique email validation.
    """
    try:
        # Check if email already exists
        existing_company = db.query(Company).filter(Company.email == signup_request.email).first()
        if existing_company:
            raise HTTPException(
                status_code=HTTPStatusCodes.BAD_REQUEST,
                detail=AuthErrorDetails.email_exists()["detail"]
            )
        
        # Validate required fields for new accounts
        missing_fields = []
        if not signup_request.name:
            missing_fields.append(AuthErrorMessages.MISSING_COMPANY_NAME.value)
        if not signup_request.first_name:
            missing_fields.append(AuthErrorMessages.MISSING_FIRST_NAME.value)
        if not signup_request.last_name:
            missing_fields.append(AuthErrorMessages.MISSING_LAST_NAME.value)
            
        if missing_fields:
            raise HTTPException(
                status_code=HTTPStatusCodes.BAD_REQUEST,
                detail=AuthErrorDetails.missing_fields(missing_fields)["detail"]
            )
        
        # Hash the password
        hashed_password = get_password_hash(signup_request.password)
        
        # Create new company
        new_company = Company(
            name=signup_request.name,
            email=signup_request.email,
            password_hash=hashed_password,
            first_name=signup_request.first_name,
            last_name=signup_request.last_name,
            phone=signup_request.phone,
            industry=signup_request.industry,
            website=signup_request.website,
            address=signup_request.address,
            role=signup_request.role,
            bio=signup_request.bio,
            profile_picture=signup_request.profile_picture
        )
        
        # Save to database
        db.add(new_company)
        db.commit()
        db.refresh(new_company)
        
        # Generate token for new company
        access_token = create_company_token(new_company.company_id, new_company.email)
        
        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            company=CompanyResponse.from_orm(new_company)
        )
            
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=HTTPStatusCodes.INTERNAL_SERVER_ERROR,
            detail=AuthErrorDetails.signup_server_error()["detail"]
        )

@router.post("/login", response_model=AuthResponse)
async def login(login_request: LoginRequest, db: Session = Depends(get_db)):
    """
    Login endpoint for existing company accounts.
    Checks credentials and returns JWT token.
    Returns generic 401 error for invalid credentials or non-existent email.
    """
    try:
        company = db.query(Company).filter(Company.email == login_request.email).first()
        
        if not company:
            # Email doesn't exist - user-friendly error
            raise HTTPException(
                status_code=HTTPStatusCodes.UNAUTHORIZED,
                detail=AuthErrorDetails.email_not_found()["detail"]
            )
        
        if not authenticate_company(login_request.password, company.password_hash):
            # Password is wrong - user-friendly error
            raise HTTPException(
                status_code=HTTPStatusCodes.UNAUTHORIZED,
                detail=AuthErrorDetails.incorrect_password()["detail"]
            )
        
        # Update last_login timestamp
        company.last_login = datetime.utcnow()
        db.commit()
        
        access_token = create_company_token(company.company_id, company.email)
        
        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            company=CompanyResponse.from_orm(company)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=HTTPStatusCodes.INTERNAL_SERVER_ERROR,
            detail=AuthErrorDetails.login_server_error()["detail"]
        )

@router.get("/me", response_model=CompanyResponse)
async def get_current_user(
    token: str = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated company information.
    """
    from helpers.auth_helper import verify_token
    
    # Verify token
    payload = verify_token(token.credentials)
    email = payload.get("sub")
    company_id = payload.get("company_id")
    
    if email is None or company_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    
    # Get company from database
    company = db.query(Company).filter(Company.company_id == company_id).first()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    return CompanyResponse.from_orm(company)

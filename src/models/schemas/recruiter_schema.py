from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class RecruiterSignupRequest(BaseModel):
    company_id: int
    first_name: str
    last_name: str
    email: str
    password: str
    role: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    profile_picture: Optional[str] = None


class RecruiterLoginRequest(BaseModel):
    email: str
    password: str


class RecruiterResponse(BaseModel):
    recruiter_id: int
    company_id: int
    first_name: str
    last_name: str
    email: str
    role: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RecruiterAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    recruiter: RecruiterResponse

from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

# Request Schemas
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    role: Optional[str] = None
    bio: Optional[str] = None
    profile_picture: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# Response Schemas
class CompanyResponse(BaseModel):
    company_id: int
    name: str
    email: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    role: Optional[str] = None
    bio: Optional[str] = None
    profile_picture: Optional[str] = None
    last_login: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    company: CompanyResponse

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class ErrorResponse(BaseModel):
    detail: str
    status_code: int

# Internal Schemas
class TokenData(BaseModel):
    email: str | None = None
    company_id: int | None = None
    type: str | None = None

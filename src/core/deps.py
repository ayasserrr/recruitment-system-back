from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from helpers.auth_helper import verify_token

security = HTTPBearer()


def get_current_company(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Validate JWT; return {company_id, recruiter_id, email}. Accepts both token types."""
    payload = verify_token(credentials.credentials)
    company_id = payload.get("company_id")
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    return {
        "company_id": int(company_id),
        "recruiter_id": payload.get("recruiter_id"),
        "email": payload.get("sub"),
    }

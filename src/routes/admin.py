"""
Admin API
─────────
Routes require a valid company-level JWT (type == "company").
Recruiter tokens are rejected — admin endpoints are company-owner only.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.orm import Session

from database.connection import get_db
from helpers.auth_helper import verify_token
from models.schemas.admin_schema import MissingKnowledgeResponse
from services.admin_service import get_missing_knowledge_report

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
_security = HTTPBearer()


def _require_company_token(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> dict:
    """
    Validates the Bearer token and enforces that it belongs to a company
    account (type == 'company'). Raises 401/403 on failure.
    """
    try:
        payload = verify_token(credentials.credentials)
    except (JWTError, HTTPException):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
        )

    if payload.get("type") != "company":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin endpoints require a company-level token.",
        )

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/admin/system-health/missing-knowledge
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.get(
    "/system-health/missing-knowledge",
    response_model=MissingKnowledgeResponse,
    summary="Knowledge Gap Report",
    description=(
        "Returns all tools that appeared in job requisitions but had no match "
        "in the knowledge DB — meaning those assessments fell back to general "
        "LLM knowledge. Ordered by occurrence count (highest first). "
        "Items with occurrence_count > 5 are flagged as high_priority."
    ),
)
def get_missing_knowledge(
    _: dict = Depends(_require_company_token),
    db: Session = Depends(get_db),
) -> MissingKnowledgeResponse:
    return get_missing_knowledge_report(db)

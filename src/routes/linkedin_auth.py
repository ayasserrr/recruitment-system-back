"""
LinkedIn OAuth 2.0 – Multi-tenant company page integration.

Flow:
  1. GET /api/v1/auth/linkedin/url          (JWT required)
     → returns the LinkedIn authorization URL.
     The current company_id is signed into the `state` parameter so
     the stateless callback can identify which company is connecting.

  2. GET /api/v1/auth/linkedin/callback     (LinkedIn redirects here)
     → exchanges `code` for access_token
     → fetches the LinkedIn Organization URN the user administers
     → upserts (company_id, token, org_id, expires_at) in company_social_auth
"""

import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database.connection import get_db
from helpers.auth_helper import verify_token, SECRET_KEY, ALGORITHM
from models.db.company_social_auth import CompanySocialAuth

router = APIRouter(prefix="/api/v1/auth/linkedin", tags=["linkedin-oauth"])
security = HTTPBearer()

# ─── LinkedIn endpoints ───────────────────────────────────────────────────────
_LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
_LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_LINKEDIN_ORG_ACLS_URL = (
    "https://api.linkedin.com/v2/organizationalEntityAcls"
    "?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED&projection=(elements*(organizationalTarget))"
)
# OpenID Connect userinfo — returns `sub` which is the LinkedIn Person ID
_LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# Scopes approved for this app (standard LinkedIn app products):
#   openid / profile / email  → OpenID Connect (sub = LinkedIn Person ID)
#   w_member_social           → post on behalf of the member (approved)
_LINKEDIN_SCOPE = "openid profile w_member_social email"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_env(key: str) -> str:
    """Return an env variable, raising a clear 500 if it is not configured."""
    value = os.getenv(key, "")
    if not value:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server misconfiguration: {key} is not set.",
        )
    return value


def _extract_company_id(token_credentials: str) -> int:
    """
    Accept either a company token or a recruiter token – both carry company_id.
    Returns the integer company_id.
    """
    payload = verify_token(token_credentials)
    company_id = payload.get("company_id")
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain a company_id.",
        )
    return int(company_id)


def _build_state(company_id: int) -> str:
    """Sign company_id + nonce into a short-lived JWT used as the OAuth `state`."""
    data = {
        "company_id": company_id,
        "nonce": secrets.token_hex(8),
        # expire in 10 minutes – enough time to complete the browser flow
        "exp": datetime.utcnow() + timedelta(minutes=10),
    }
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def _decode_state(state: str) -> int:
    """Verify and decode the OAuth `state` token; return company_id."""
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["company_id"])
    except (JWTError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter.",
        )


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/url")
def get_linkedin_auth_url(token: str = Depends(security)):
    """
    Returns the LinkedIn OAuth authorization URL.
    The recruiter opens this URL in the browser to connect their LinkedIn
    company page. Requires a valid JWT (company or recruiter).
    """
    company_id = _extract_company_id(token.credentials)
    client_id = _get_env("LINKEDIN_CLIENT_ID")
    redirect_uri = _get_env("LINKEDIN_REDIRECT_URI")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _LINKEDIN_SCOPE,
        "state": _build_state(company_id),
        "prompt": "login",
    }
    url = f"{_LINKEDIN_AUTH_URL}?{urlencode(params)}"
    return {"authorization_url": url}


@router.get("/callback")
async def linkedin_callback(
    state: str = Query(..., description="State parameter for CSRF protection"),
    db: Session = Depends(get_db),
    # LinkedIn sends either `code` (success) or `error` + `error_description` (denied/failed)
    code: str | None = Query(default=None, description="Authorization code returned by LinkedIn"),
    error: str | None = Query(default=None, description="Error code returned by LinkedIn"),
    error_description: str | None = Query(default=None, description="Human-readable error from LinkedIn"),
):
    """
    LinkedIn redirects here after the user grants (or denies) access.
    1. Checks for an error response and returns a clean 400 if present.
    2. Verifies the signed `state` to identify the company.
    3. Exchanges the `code` for an access token.
    4. Fetches the LinkedIn Organization URN the user administers.
    5. Upserts the credentials in `company_social_auth`.
    """
    # 0. LinkedIn denied / scope not approved → surface a clean error.
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"LinkedIn authorization error – {error}: {error_description or 'no details provided'}.",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code in LinkedIn callback.",
        )

    # 1. Recover company_id from signed state (no JWT header needed here –
    #    the state itself is the proof of the original authenticated request).
    company_id = _decode_state(state)

    client_id = _get_env("LINKEDIN_CLIENT_ID")
    client_secret = _get_env("LINKEDIN_CLIENT_SECRET")
    redirect_uri = _get_env("LINKEDIN_REDIRECT_URI")

    async with httpx.AsyncClient(timeout=15) as client:
        # 2. Exchange code → access token
        token_resp = await client.post(
            _LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LinkedIn token exchange failed: {token_resp.text}",
        )

    token_data = token_resp.json()
    access_token: str = token_data["access_token"]
    expires_in: int = token_data.get("expires_in", 5184000)  # default 60 days
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # 3. Fetch the LinkedIn Person ID via OpenID Connect userinfo (`sub` field).
    #    This is needed for personal-profile publishing (w_member_social scope).
    async with httpx.AsyncClient(timeout=15) as client:
        userinfo_resp = await client.get(_LINKEDIN_USERINFO_URL, headers=auth_headers)

    provider_user_id: str | None = None
    if userinfo_resp.status_code == 200:
        provider_user_id = userinfo_resp.json().get("sub")

    # 4. Fetch the Organization URN this user administers (best-effort).
    async with httpx.AsyncClient(timeout=15) as client:
        acl_resp = await client.get(_LINKEDIN_ORG_ACLS_URL, headers=auth_headers)

    organization_id: str | None = None
    if acl_resp.status_code == 200:
        elements = acl_resp.json().get("elements", [])
        if elements:
            organization_id = elements[0].get("organizationalTarget")
    # If the call fails or returns no orgs we still save the token –
    # the organization_id can be set later once the account is promoted.

    # 5. Upsert company_social_auth — scoped strictly to this company_id
    record = _get_record_for_company(company_id, db)
    if record:
        record.linkedin_access_token = access_token
        record.linkedin_organization_id = organization_id
        record.provider_user_id = provider_user_id
        record.expires_at = expires_at
        record.is_active = True
    else:
        record = CompanySocialAuth(
            company_id=company_id,
            linkedin_access_token=access_token,
            linkedin_organization_id=organization_id,
            provider_user_id=provider_user_id,
            expires_at=expires_at,
            is_active=True,
        )
        db.add(record)

    db.commit()
    db.refresh(record)

    return {
        "message": "LinkedIn account connected successfully.",
        "company_id": company_id,
        "provider_user_id": provider_user_id,
        "linkedin_organization_id": organization_id,
        "expires_at": expires_at.isoformat(),
    }


def _get_record_for_company(company_id: int, db: Session) -> CompanySocialAuth | None:
    """Single, reusable query — ALWAYS scoped to company_id. Never call without it."""
    return (
        db.query(CompanySocialAuth)
        .filter(CompanySocialAuth.company_id == company_id)
        .first()
    )


@router.get("/status")
def get_linkedin_status(
    token: str = Depends(security),
    db: Session = Depends(get_db),
):
    """
    Returns the LinkedIn connection status for the currently authenticated company.
    Every query is scoped to the company_id in the JWT — other companies' data
    is never returned.
    """
    company_id = _extract_company_id(token.credentials)
    record = _get_record_for_company(company_id, db)

    if not record or not record.is_active:
        return {
            "connected": False,
            "company_id": company_id,
            "linkedin_organization_id": None,
            "expires_at": None,
        }

    return {
        "connected": True,
        "company_id": company_id,
        # Never expose the raw access token to the frontend
        "linkedin_organization_id": record.linkedin_organization_id,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
    }


@router.delete("/disconnect")
def disconnect_linkedin(
    token: str = Depends(security),
    db: Session = Depends(get_db),
):
    """
    Unlinks the LinkedIn account for the currently authenticated company.
    Deletes the company_social_auth record scoped to the company_id in the JWT.
    """
    company_id = _extract_company_id(token.credentials)

    record = _get_record_for_company(company_id, db)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No LinkedIn connection found for this company.",
        )

    db.delete(record)
    db.commit()

    return {"message": "LinkedIn account unlinked successfully."}


@router.patch("/organization")
def set_organization_id(
    linkedin_organization_id: str = Query(..., description="LinkedIn Organization URN, e.g. urn:li:organization:12345678"),
    token: str = Depends(security),
    db: Session = Depends(get_db),
):
    """
    Plan B: manually set the LinkedIn Organization URN for the current company.
    Use this when the automated fetch via organizationalEntityAcls is restricted
    (e.g. w_member_social scope does not include org-level ACL access).

    The frontend can call this endpoint with the Organization URN after the
    OAuth callback succeeds.
    """
    company_id = _extract_company_id(token.credentials)

    record = _get_record_for_company(company_id, db)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No LinkedIn connection found for this company. Complete the OAuth flow first.",
        )

    record.linkedin_organization_id = linkedin_organization_id
    db.commit()
    db.refresh(record)

    return {
        "message": "LinkedIn organization ID updated.",
        "company_id": company_id,
        "linkedin_organization_id": record.linkedin_organization_id,
    }

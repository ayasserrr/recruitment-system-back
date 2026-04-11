"""
LangGraph workflow for LinkedIn post publishing.

Nodes
─────
context_loader
    Fetches the company's LinkedIn credentials (access_token,
    organization_id) from company_social_auth and the generated post
    content from posting_platforms (platform_name = 'LinkedIn').
    Short-circuits to finalizer on any error so the DB is always updated.

api_publisher
    Calls the LinkedIn UGC Posts API.
    Handles expired-token detection and other API errors explicitly —
    these are surfaced as state["error"] rather than raised exceptions,
    because retrying with a bad token is pointless.

finalizer
    Writes the outcome back to the database:
      • success → JobRequisition.status = 'published'
                  PostingPlatform.posted_at  = utcnow()
                  PostingPlatform.status     = 'Published'
      • failure → JobRequisition.status = 'failed'
                  PostingPlatform.status     = 'Failed'

State
─────
LinkedInPublishState (TypedDict) — immutable between nodes; each node
returns a *new* dict via {**state, key: value} to keep the graph
functional and easy to test.
"""

import logging
from datetime import datetime
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from database.connection import SessionLocal
from models.db.company_social_auth import CompanySocialAuth
from models.db.job_requisition import JobRequisition
from models.db.posting_platform import PostingPlatform

logger = logging.getLogger(__name__)

# LinkedIn REST Posts API (newer, works with w_member_social scope)
_LINKEDIN_POSTS_URL = "https://api.linkedin.com/rest/posts"
_LINKEDIN_API_VERSION = "202504"   # keep current; bump quarterly if LinkedIn deprecates


# ── State schema ──────────────────────────────────────────────────────────────

class LinkedInPublishState(TypedDict):
    jr_id: int
    company_id: int
    # populated by context_loader
    access_token: Optional[str]
    author_urn: Optional[str]       # urn:li:person:XXX  (personal profile fallback)
    organization_id: Optional[str]  # urn:li:organization:XXX  (may be None — that's OK)
    post_content: Optional[str]
    # set by any node on error; checked by finalizer
    error: Optional[str]
    # set True by api_publisher on HTTP 201
    success: bool


# ── Node 1: Context Loader ────────────────────────────────────────────────────

def context_loader(state: LinkedInPublishState) -> LinkedInPublishState:
    """
    Load LinkedIn credentials and post content.

    Author resolution (personal-profile fallback)
    ─────────────────────────────────────────────
    We prefer posting to the LinkedIn Organization page, but the current
    app tier only has w_member_social approved, which covers personal
    profiles only.  Resolution order:

      1. organization_id is set  → author = urn:li:organization:XXX
      2. provider_user_id is set → author = urn:li:person:XXX   ← demo path
      3. neither                 → short-circuit to finalizer with error

    Short-circuits on:
      • No active auth record
      • Expired token
      • No LinkedIn post content for this JR
    """
    jr_id = state["jr_id"]
    company_id = state["company_id"]

    db = SessionLocal()
    try:
        # ── credentials ───────────────────────────────────────────────────────
        social_auth: Optional[CompanySocialAuth] = (
            db.query(CompanySocialAuth)
            .filter(
                CompanySocialAuth.company_id == company_id,
                CompanySocialAuth.is_active.is_(True),
            )
            .first()
        )

        if not social_auth:
            return {
                **state,
                "error": f"No active LinkedIn auth found for company {company_id}.",
                "success": False,
            }

        if social_auth.expires_at and social_auth.expires_at < datetime.utcnow():
            return {
                **state,
                "error": (
                    f"LinkedIn access token for company {company_id} has expired "
                    f"(expired at {social_auth.expires_at.isoformat()}). "
                    "Re-authenticate via /api/v1/auth/linkedin/url."
                ),
                "success": False,
            }

        # ── resolve author URN ────────────────────────────────────────────────
        if social_auth.linkedin_organization_id:
            author_urn = social_auth.linkedin_organization_id   # already a full URN
        elif social_auth.provider_user_id:
            author_urn = f"urn:li:person:{social_auth.provider_user_id}"
            logger.info(
                "[context_loader] No org ID for company %s — falling back to "
                "personal profile author: %s",
                company_id, author_urn,
            )
        else:
            return {
                **state,
                "error": (
                    "Neither linkedin_organization_id nor provider_user_id is set "
                    f"for company {company_id}. Complete the OAuth flow or set the "
                    "org ID via PATCH /api/v1/auth/linkedin/organization."
                ),
                "success": False,
            }

        # ── post content ──────────────────────────────────────────────────────
        platform: Optional[PostingPlatform] = (
            db.query(PostingPlatform)
            .filter(
                PostingPlatform.requisition_id == jr_id,
                PostingPlatform.platform_name.ilike("linkedin"),
            )
            .first()
        )

        if not platform or not platform.platform_post_content:
            return {
                **state,
                "error": (
                    f"No LinkedIn post content found for JR {jr_id}. "
                    "Run the job-posting generation pipeline first."
                ),
                "success": False,
            }

        logger.info(
            "[context_loader] Credentials and content loaded for JR %s (company=%s). "
            "Author: %s",
            jr_id, company_id, author_urn,
        )
        return {
            **state,
            "access_token": social_auth.linkedin_access_token,
            "author_urn": author_urn,
            "organization_id": social_auth.linkedin_organization_id,
            "post_content": platform.platform_post_content,
        }

    finally:
        db.close()


# ── Node 2: API Publisher ─────────────────────────────────────────────────────

def api_publisher(state: LinkedInPublishState) -> LinkedInPublishState:
    """
    POST the job announcement to LinkedIn via the REST Posts API.

    Uses the resolved author_urn from context_loader:
      • urn:li:organization:XXX  — company page  (requires Marketing Developer Platform)
      • urn:li:person:XXX        — personal feed (w_member_social scope — demo path)

    Endpoint: POST https://api.linkedin.com/rest/posts
    Expects HTTP 201 Created on success.

    Error handling
    ──────────────
    • 401  — token expired/revoked → error, no retry.
    • 403  — scope/permission issue → logs a clear diagnostic, no retry.
    • other non-2xx → error with body snippet; Celery will retry on exception.
    • Timeout → error message; Celery task retries.
    """
    access_token = state["access_token"]
    author_urn = state["author_urn"]
    post_content = state["post_content"]

    payload = {
        "author": author_urn,
        "commentary": post_content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    try:
        response = httpx.post(
            _LINKEDIN_POSTS_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": _LINKEDIN_API_VERSION,
            },
            timeout=15,
        )
    except httpx.TimeoutException:
        return {
            **state,
            "error": "LinkedIn API request timed out after 15 s.",
            "success": False,
        }
    except Exception as exc:
        return {
            **state,
            "error": f"Unexpected network error calling LinkedIn API: {exc}",
            "success": False,
        }

    if response.status_code == 201:
        logger.info(
            "[api_publisher] JR %s posted to LinkedIn successfully (HTTP 201). "
            "Author: %s",
            state["jr_id"], author_urn,
        )
        return {**state, "success": True}

    # ── non-201 responses ─────────────────────────────────────────────────────
    if response.status_code == 401:
        error_msg = (
            "LinkedIn API 401 Unauthorized — token is expired or revoked. "
            "Re-authenticate via /api/v1/auth/linkedin/url."
        )
    elif response.status_code == 403:
        error_msg = (
            f"LinkedIn API 403 Forbidden — scope/permission denied for author {author_urn}. "
            "This is a LinkedIn app permission issue, not a code bug. "
            "For org pages, the 'Marketing Developer Platform' product is required. "
            f"Response: {response.text[:300]}"
        )
    else:
        error_msg = (
            f"LinkedIn API HTTP {response.status_code}: {response.text[:500]}"
        )

    logger.error("[api_publisher] JR %s — %s", state["jr_id"], error_msg)
    return {**state, "error": error_msg, "success": False}


# ── Node 3: Finalizer ─────────────────────────────────────────────────────────

def finalizer(state: LinkedInPublishState) -> LinkedInPublishState:
    """
    Persist the publishing outcome to the database.

    Success path
    ────────────
      JobRequisition.status       = 'published'
      PostingPlatform.status      = 'Published'
      PostingPlatform.posted_at   = utcnow()

    Failure path
    ────────────
      JobRequisition.status       = 'failed'
      PostingPlatform.status      = 'Failed'
    """
    jr_id = state["jr_id"]
    success = state.get("success", False)
    error = state.get("error")
    now = datetime.utcnow()

    db = SessionLocal()
    try:
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == jr_id)
            .first()
        )

        if not jr:
            logger.error("[finalizer] JobRequisition %s not found — cannot update status.", jr_id)
            return state

        platform: Optional[PostingPlatform] = (
            db.query(PostingPlatform)
            .filter(
                PostingPlatform.requisition_id == jr_id,
                PostingPlatform.platform_name.ilike("linkedin"),
            )
            .first()
        )

        if success:
            jr.status = "published"
            if platform:
                platform.status = "Published"
                platform.posted_at = now
            logger.info("[finalizer] JR %s marked as published.", jr_id)
        else:
            jr.status = "failed"
            if platform:
                platform.status = "Failed"
            logger.error("[finalizer] JR %s marked as failed. Reason: %s", jr_id, error)

        db.commit()

    except Exception:
        db.rollback()
        logger.exception("[finalizer] DB error while updating status for JR %s.", jr_id)
    finally:
        db.close()

    return state


# ── Routing helpers ───────────────────────────────────────────────────────────

def _route_after_loader(state: LinkedInPublishState) -> str:
    """Skip api_publisher and go straight to finalizer if context loading failed."""
    return "finalizer" if state.get("error") else "api_publisher"


# ── Graph builder ─────────────────────────────────────────────────────────────

def _build_graph() -> object:
    graph = StateGraph(LinkedInPublishState)

    graph.add_node("context_loader", context_loader)
    graph.add_node("api_publisher", api_publisher)
    graph.add_node("finalizer", finalizer)

    graph.set_entry_point("context_loader")

    graph.add_conditional_edges(
        "context_loader",
        _route_after_loader,
        {
            "api_publisher": "api_publisher",
            "finalizer": "finalizer",
        },
    )

    # api_publisher always goes to finalizer (success or failure)
    graph.add_edge("api_publisher", "finalizer")
    graph.add_edge("finalizer", END)

    return graph.compile()


# Module-level singleton — compiled once, reused across all Celery worker invocations
_compiled_graph = None


def run_linkedin_publishing_graph(jr_id: int, company_id: int) -> LinkedInPublishState:
    """
    Entry point called by the Celery worker task.

    Builds (or reuses) the compiled LangGraph and runs it with the
    initial state for the given job requisition and company.

    Returns the final state dict so the caller can log success/failure.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()

    initial_state: LinkedInPublishState = {
        "jr_id": jr_id,
        "company_id": company_id,
        "access_token": None,
        "author_urn": None,
        "organization_id": None,
        "post_content": None,
        "error": None,
        "success": False,
    }

    logger.info(
        "[linkedin_graph] Invoking publishing graph for JR %s (company=%s).",
        jr_id,
        company_id,
    )
    result: LinkedInPublishState = _compiled_graph.invoke(initial_state)
    logger.info(
        "[linkedin_graph] Graph finished for JR %s — success=%s.",
        jr_id,
        result.get("success"),
    )
    return result

"""
LangGraph workflow for LinkedIn post publishing.

Nodes
─────
context_loader
    Fetches the company's LinkedIn credentials (access_token,
    organization_id) from company_social_auth and the generated post
    content from posting_platforms (platform_name = 'LinkedIn').
    Appends the dynamic application URL to the post content.
    Short-circuits to finalizer on any error so the DB is always updated.

preflight_check  ← SAFETY GUARD
    Runs AFTER context_loader, BEFORE the LinkedIn API call.
    Validates three hard requirements:
      1. company_id is a positive integer that exists in the DB.
      2. jr_id (job_id) is a positive integer that exists in the DB.
      3. post_content contains the correctly-formed apply URL
         (/apply?cid=<company_id>&jid=<jr_id>), proving the link was
         injected — not just that APP_BASE_URL is set.
    On ANY failure:
      • Sets state["error"] and state["preflight_failed"] = True.
      • Routes straight to finalizer, which SKIPS all DB status writes
        (JR stays "Active") so Celery can retry once the issue is fixed.

api_publisher
    Calls the LinkedIn REST Posts API.
    Handles expired-token detection and other API errors explicitly —
    these are surfaced as state["error"] rather than raised exceptions,
    because retrying with a bad token is pointless.

finalizer
    Writes the outcome back to the database.

    preflight_failed = True  → NO DB writes; logs an actionable error.
                               JR stays "Active" so Celery retries.
    success = True           → JobRequisition.status = 'published'
                               PostingPlatform.status = 'Published'
                               PostingPlatform.posted_at = utcnow()
    success = False          → JobRequisition.status = 'failed'
                               PostingPlatform.status = 'Failed'

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
from helpers.config import get_settings
from models.db.company import Company
from models.db.company_social_auth import CompanySocialAuth
from models.db.job_requisition import JobRequisition
from models.db.posting_platform import PostingPlatform
from models.db.recruiter import Recruiter
from services.email_service import send_post_live_notification

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
    # True when preflight_check fails — finalizer must NOT touch DB status
    preflight_failed: bool
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

        # ── append application link ───────────────────────────────────────────
        app_base_url = get_settings().APP_BASE_URL.rstrip("/")
        apply_url = f"{app_base_url}/apply?cid={company_id}&jid={jr_id}"
        post_content = (
            f"{platform.platform_post_content}\n\n"
            f"Apply now: {apply_url}"
        )

        logger.info(
            "[context_loader] Credentials and content loaded for JR %s (company=%s). "
            "Author: %s | Apply URL: %s",
            jr_id, company_id, author_urn, apply_url,
        )
        return {
            **state,
            "access_token": social_auth.linkedin_access_token,
            "author_urn": author_urn,
            "organization_id": social_auth.linkedin_organization_id,
            "post_content": post_content,
        }

    finally:
        db.close()


# ── Node 2: Preflight Check (Safety Guard) ────────────────────────────────────

def preflight_check(state: LinkedInPublishState) -> LinkedInPublishState:
    """
    Safety guard — runs AFTER context_loader, BEFORE the LinkedIn API call.

    Hard requirements (all three must pass):
      1. company_id > 0  AND  the company row exists in the DB.
      2. jr_id > 0  AND  the job requisition row exists in the DB.
      3. post_content contains the exact apply URL pattern
         '/apply?cid=<company_id>&jid=<jr_id>', confirming the link was
         injected by context_loader with the correct IDs.

    Failure behaviour
    ─────────────────
    Sets preflight_failed = True so the finalizer knows to leave the JR
    status untouched (stays 'Active').  The Celery beat will retry the
    task on its next tick once the underlying issue is resolved — no
    manual reset needed.
    """
    jr_id = state["jr_id"]
    company_id = state["company_id"]
    post_content = state.get("post_content") or ""

    # ── 1. company_id sanity ──────────────────────────────────────────────────
    if not isinstance(company_id, int) or company_id <= 0:
        msg = (
            f"[preflight] BLOCKED — company_id '{company_id}' is not a valid positive integer."
        )
        logger.error(msg)
        return {**state, "error": msg, "preflight_failed": True, "success": False}

    # ── 2. jr_id sanity ──────────────────────────────────────────────────────
    if not isinstance(jr_id, int) or jr_id <= 0:
        msg = (
            f"[preflight] BLOCKED — jr_id '{jr_id}' is not a valid positive integer."
        )
        logger.error(msg)
        return {**state, "error": msg, "preflight_failed": True, "success": False}

    db = SessionLocal()
    try:
        # ── 3. company exists in DB ───────────────────────────────────────────
        company_exists = (
            db.query(Company.company_id)
            .filter(Company.company_id == company_id)
            .first()
        )
        if not company_exists:
            msg = (
                f"[preflight] BLOCKED — company_id {company_id} does not exist in the database."
            )
            logger.error(msg)
            return {**state, "error": msg, "preflight_failed": True, "success": False}

        # ── 4. job requisition exists in DB ──────────────────────────────────
        jr_exists = (
            db.query(JobRequisition.requisition_id)
            .filter(
                JobRequisition.requisition_id == jr_id,
                JobRequisition.company_id == company_id,
            )
            .first()
        )
        if not jr_exists:
            msg = (
                f"[preflight] BLOCKED — requisition {jr_id} does not exist "
                f"or does not belong to company {company_id}."
            )
            logger.error(msg)
            return {**state, "error": msg, "preflight_failed": True, "success": False}

    finally:
        db.close()

    # ── 5. apply URL is present in post content ───────────────────────────────
    expected_url_fragment = f"/apply?cid={company_id}&jid={jr_id}"
    if expected_url_fragment not in post_content:
        msg = (
            f"[preflight] BLOCKED — apply URL '{expected_url_fragment}' is missing from "
            f"the post content for JR {jr_id}. "
            "Ensure APP_BASE_URL is set in .env and the Celery worker was restarted "
            "after the link-injection code was deployed."
        )
        logger.error(msg)
        return {**state, "error": msg, "preflight_failed": True, "success": False}

    logger.info(
        "[preflight] All checks passed for JR %s (company=%s). Proceeding to publish.",
        jr_id, company_id,
    )
    return state


# ── Node 3: API Publisher ─────────────────────────────────────────────────────

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


# ── Email helper ─────────────────────────────────────────────────────────────

def _notify_recruiter(jr: JobRequisition, db) -> None:
    """
    Resolve the best recipient email for a published JR and fire the
    post-live notification in a background thread.

    Resolution order
    ────────────────
    1. jr.contact_email  — explicit contact set on the requisition (primary)
    2. recruiter.email   — the recruiter who owns the JR
    3. company.email     — the company account email (last resort)
    """
    recipient_email: Optional[str] = None
    recipient_name: str = "HR Team"

    # 1. contact_email on the JR (primary — set explicitly by the recruiter)
    if jr.contact_email:
        recipient_email = jr.contact_email
        # Try to get a name from the recruiter to personalise the greeting
        if jr.recruiter_id:
            recruiter: Optional[Recruiter] = (
                db.query(Recruiter)
                .filter(Recruiter.recruiter_id == jr.recruiter_id)
                .first()
            )
            if recruiter:
                recipient_name = f"{recruiter.first_name} {recruiter.last_name}".strip()

    # 2. Recruiter email
    if not recipient_email and jr.recruiter_id:
        recruiter = (
            db.query(Recruiter)
            .filter(Recruiter.recruiter_id == jr.recruiter_id)
            .first()
        )
        if recruiter and recruiter.email:
            recipient_email = recruiter.email
            recipient_name = f"{recruiter.first_name} {recruiter.last_name}".strip()

    # 3. Company account email
    if not recipient_email:
        company: Optional[Company] = (
            db.query(Company)
            .filter(Company.company_id == jr.company_id)
            .first()
        )
        if company and company.email:
            recipient_email = company.email
            recipient_name = company.name or recipient_name

    if not recipient_email:
        logger.warning(
            "[finalizer] No recipient email found for JR %s — skipping notification.",
            jr.requisition_id,
        )
        return

    app_base_url = get_settings().APP_BASE_URL.rstrip("/")
    apply_url = f"{app_base_url}/apply?cid={jr.company_id}&jid={jr.requisition_id}"

    logger.info(
        "[finalizer] Sending post-live notification for JR %s ('%s') → %s",
        jr.requisition_id, jr.job_title, recipient_email,
    )
    send_post_live_notification(
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        job_title=jr.job_title,
        apply_url=apply_url,
    )


# ── Node 4: Finalizer ─────────────────────────────────────────────────────────

def finalizer(state: LinkedInPublishState) -> LinkedInPublishState:
    """
    Persist the publishing outcome to the database.

    preflight_failed = True
        → NO DB writes at all.  JR status stays 'Active' so the Celery
          beat retries automatically once the configuration issue is fixed.
          The error is logged at ERROR level with a clear action item.

    success = True
        → JobRequisition.status       = 'published'
           PostingPlatform.status      = 'Published'
           PostingPlatform.posted_at   = utcnow()

    success = False  (API / network error)
        → JobRequisition.status       = 'failed'
           PostingPlatform.status      = 'Failed'
    """
    jr_id = state["jr_id"]
    success = state.get("success", False)
    error = state.get("error")
    preflight_failed = state.get("preflight_failed", False)

    # ── preflight failure: do not touch DB, surface a loud actionable log ─────
    if preflight_failed:
        logger.error(
            "[finalizer] PREFLIGHT GUARD — publishing ABORTED for JR %s. "
            "JR status left unchanged (Active). Fix the issue and let Celery retry. "
            "Reason: %s",
            jr_id, error,
        )
        return state

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

            # ── fire post-live email notification ─────────────────────────────
            try:
                _notify_recruiter(jr=jr, db=db)
            except Exception:
                logger.warning(
                    "[finalizer] Could not send email notification for JR %s.",
                    jr_id, exc_info=True,
                )
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
    """Skip preflight+publisher and go straight to finalizer if context loading failed."""
    return "finalizer" if state.get("error") else "preflight_check"


def _route_after_preflight(state: LinkedInPublishState) -> str:
    """Block the API call and go to finalizer if any preflight check failed."""
    return "finalizer" if state.get("preflight_failed") else "api_publisher"


# ── Graph builder ─────────────────────────────────────────────────────────────

def _build_graph() -> object:
    graph = StateGraph(LinkedInPublishState)

    graph.add_node("context_loader", context_loader)
    graph.add_node("preflight_check", preflight_check)
    graph.add_node("api_publisher", api_publisher)
    graph.add_node("finalizer", finalizer)

    graph.set_entry_point("context_loader")

    graph.add_conditional_edges(
        "context_loader",
        _route_after_loader,
        {
            "preflight_check": "preflight_check",
            "finalizer": "finalizer",
        },
    )

    graph.add_conditional_edges(
        "preflight_check",
        _route_after_preflight,
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
        "preflight_failed": False,
        "success": False,
    }

    logger.info(
        "[linkedin_graph] Invoking publishing graph for JR %s (company=%s).",
        jr_id,
        company_id,
    )
    result: LinkedInPublishState = _compiled_graph.invoke(initial_state)
    logger.info(
        "[linkedin_graph] Graph finished for JR %s — success=%s, preflight_failed=%s.",
        jr_id,
        result.get("success"),
        result.get("preflight_failed"),
    )
    return result

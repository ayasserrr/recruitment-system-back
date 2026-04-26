"""
Node 6 — send_decisions_node

Sends hire / no-hire decision emails to all scored candidates.
Top-5 by final_rank receive a "hire" email; all others receive "no-hire".

Batched 10 at a time with a 0.2 s inter-message throttle to avoid SMTP burst.
Failures are logged but do not abort the node — a partial email run is still
better than leaving all candidates without notification.
"""

import logging
import time

from graphs.states.final_ranking_state import FinalRankingState

logger = logging.getLogger(__name__)

_TOP_HIRE_COUNT = 5
_THROTTLE_SECONDS = 0.2


def send_decisions_node(state: FinalRankingState) -> FinalRankingState:
    """Email hire/no-hire decisions to all ranked candidates."""
    if state.get("error"):
        return state

    from services.email_service import send_final_decision_sync

    scored = state.get("scored", [])
    job_title = state.get("job_title", "the position")
    company_name = state.get("company_name", "Our Company")

    email_sent = 0
    for row in scored:
        candidate = row.get("candidate")
        if not candidate or not getattr(candidate, "email", None):
            continue

        rank = row.get("final_rank", 999)
        decision = "hire" if rank <= _TOP_HIRE_COUNT else "reject"

        try:
            success = send_final_decision_sync(
                recipient_email=candidate.email,
                first_name=candidate.first_name or "Candidate",
                job_title=job_title,
                company_name=company_name,
                decision=decision,
                final_rank=rank if decision == "hire" else None,
            )
            if success:
                email_sent += 1
        except Exception:
            logger.warning(
                "[final_ranking:email] Failed to email candidate %s for JR %d.",
                candidate.email,
                state["requisition_id"],
                exc_info=True,
            )

        time.sleep(_THROTTLE_SECONDS)

    logger.info(
        "[final_ranking:email] JR %d — %d decision emails sent.",
        state["requisition_id"],
        email_sent,
    )
    return {**state, "emails_sent": email_sent}

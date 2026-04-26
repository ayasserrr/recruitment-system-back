"""
Post-deadline pool processing graph runner.

Delegates to services.post_deadline_graph.run_post_deadline_graph().

Entry point used by tasks.assessment_tasks.process_assessment_ranking.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_post_deadline(requisition_id: int) -> dict[str, Any]:
    """
    Execute the 4-node post-deadline workflow (no-show marking + pool ranking).

    Returns the final PostDeadlineState dict.
    """
    from services.post_deadline_graph import run_post_deadline_graph
    logger.info("[post_deadline_runner] Starting for JR %d.", requisition_id)
    return run_post_deadline_graph(requisition_id=requisition_id)

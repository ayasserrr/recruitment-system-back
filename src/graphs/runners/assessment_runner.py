"""
Assessment generation graph runner.

Delegates to services.assessment_graph.run_assessment_graph().

Entry point used by tasks.assessment_tasks.process_assessment_generation.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_assessment(requisition_id: int) -> dict[str, Any]:
    """
    Execute the 4-node assessment generation LangGraph.

    Returns the final AssessmentState dict.
    Check state.get("error") for failures.
    """
    from services.assessment_graph import run_assessment_graph
    logger.info("[assessment_runner] Starting for JR %d.", requisition_id)
    return run_assessment_graph(requisition_id=requisition_id)

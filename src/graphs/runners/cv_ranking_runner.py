"""
CV Ranking graph runner.

Delegates to the existing services.ranking_graph.run_ranking_graph()
implementation until the nodes are incrementally migrated into
src/graphs/nodes/cv_ranking/.

Entry point used by tasks.ranking_tasks.process_cv_ranking.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_cv_ranking(requisition_id: int) -> dict[str, Any]:
    """
    Execute the 6-node CV ranking LangGraph for a single JobRequisition.

    Returns the final state dict.  Check state.get("error") for graph-level
    failures; check state.get("ranked_candidates", []) for results.
    """
    from services.ranking_graph import run_ranking_graph  # lazy import — heavy module
    logger.info("[cv_ranking_runner] Starting for JR %d.", requisition_id)
    return run_ranking_graph(requisition_id=requisition_id)

"""
Final Ranking graph runner.

This is the first runner that uses the proper graphs/nodes/ structure
(rather than delegating to a legacy service file).  The graph wires up
the 6 final_ranking nodes into a LangGraph and executes them sequentially.

Entry point used by tasks.interview_tasks.compute_final_ranking.
"""

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from graphs.nodes.final_ranking.gather_applications import gather_applications_node
from graphs.nodes.final_ranking.compute_weights import compute_weights_node
from graphs.nodes.final_ranking.score_candidates import score_candidates_node
from graphs.nodes.final_ranking.sort_and_rank import sort_and_rank_node
from graphs.nodes.final_ranking.persist_rankings import persist_rankings_node
from graphs.nodes.final_ranking.send_decisions import send_decisions_node
from graphs.states.final_ranking_state import FinalRankingState

logger = logging.getLogger(__name__)


def _route_on_error(state: FinalRankingState) -> str:
    """Route to END immediately if any node has set an error."""
    return "end" if state.get("error") else "continue"


def _build_final_ranking_graph() -> StateGraph:
    g = StateGraph(FinalRankingState)

    g.add_node("gather_applications", gather_applications_node)
    g.add_node("compute_weights", compute_weights_node)
    g.add_node("score_candidates", score_candidates_node)
    g.add_node("sort_and_rank", sort_and_rank_node)
    g.add_node("persist_rankings", persist_rankings_node)
    g.add_node("send_decisions", send_decisions_node)

    g.set_entry_point("gather_applications")

    # After each node: abort to END on error, else continue
    for src, dst in [
        ("gather_applications", "compute_weights"),
        ("compute_weights", "score_candidates"),
        ("score_candidates", "sort_and_rank"),
        ("sort_and_rank", "persist_rankings"),
        ("persist_rankings", "send_decisions"),
    ]:
        g.add_conditional_edges(
            src,
            _route_on_error,
            {"end": END, "continue": dst},
        )

    g.add_edge("send_decisions", END)
    return g


# Compile once at module import — reused for every invocation.
_compiled_graph = _build_final_ranking_graph().compile()


def run_final_ranking(requisition_id: int) -> dict[str, Any]:
    """
    Execute the 6-node Final Ranking LangGraph for a single JobRequisition.

    Returns the final FinalRankingState dict.
    Check state.get("error") for graph-level failures.
    """
    logger.info("[final_ranking_runner] Starting for JR %d.", requisition_id)

    initial_state: FinalRankingState = {
        "requisition_id": requisition_id,
        "posting_id": None,
        "job_title": None,
        "company_name": None,
        "applications": [],
        "has_assessment": False,
        "has_interview": False,
        "weights": {},
        "scored": [],
        "rankings_persisted": False,
        "emails_sent": 0,
        "error": None,
    }

    result = _compiled_graph.invoke(initial_state)
    return result

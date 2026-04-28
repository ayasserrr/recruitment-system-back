"""
Final Ranking graph runner — 4-Stage Pipeline.

8-node LangGraph that implements the full candidate ranking workflow.

Stage order and weights (default, proportionally redistributed when absent):
    Stage 1 — Screening (CV Match)         20%
    Stage 2 — Technical Assessment          25%
    Stage 3 — Technical Interview           30%
    Stage 4 — HR Interview (Soft Skills)    25%

Node sequence:
    gather_applications → compute_weights → tech_interview → hr_analysis →
    score_candidates → sort_and_rank → persist_rankings → send_decisions

Entry point used by services.ranking_service and Celery tasks.

Graph is compiled lazily on first call so that ML model imports do not
block server startup.
"""

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from graphs.nodes.final_ranking.gather_applications import gather_applications_node
from graphs.nodes.final_ranking.compute_weights import compute_weights_node
from graphs.nodes.final_ranking.tech_interview_node import tech_interview_node
from graphs.nodes.final_ranking.hr_analysis_node import hr_analysis_node
from graphs.nodes.final_ranking.score_candidates import score_candidates_node
from graphs.nodes.final_ranking.sort_and_rank import sort_and_rank_node
from graphs.nodes.final_ranking.persist_rankings import persist_rankings_node
from graphs.nodes.final_ranking.send_decisions import send_decisions_node
from graphs.states.final_ranking_state import FinalRankingState

logger = logging.getLogger(__name__)

# Lazy-compiled — built on first call to run_final_ranking()
_compiled_graph = None


def _route_on_error(state: FinalRankingState) -> str:
    """Abort to END immediately if any node set an error."""
    return "end" if state.get("error") else "continue"


def _build_final_ranking_graph() -> StateGraph:
    g = StateGraph(FinalRankingState)

    g.add_node("gather_applications", gather_applications_node)
    g.add_node("compute_weights",     compute_weights_node)
    g.add_node("tech_interview",      tech_interview_node)
    g.add_node("hr_analysis",         hr_analysis_node)
    g.add_node("score_candidates",    score_candidates_node)
    g.add_node("sort_and_rank",       sort_and_rank_node)
    g.add_node("persist_rankings",    persist_rankings_node)
    g.add_node("send_decisions",      send_decisions_node)

    g.set_entry_point("gather_applications")

    # Each node either continues to the next or exits on error
    for src, dst in [
        ("gather_applications", "compute_weights"),
        ("compute_weights",     "tech_interview"),
        ("tech_interview",      "hr_analysis"),
        ("hr_analysis",         "score_candidates"),
        ("score_candidates",    "sort_and_rank"),
        ("sort_and_rank",       "persist_rankings"),
        ("persist_rankings",    "send_decisions"),
    ]:
        g.add_conditional_edges(
            src,
            _route_on_error,
            {"end": END, "continue": dst},
        )

    g.add_edge("send_decisions", END)
    return g


def _get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_final_ranking_graph().compile()
    return _compiled_graph


def run_final_ranking(requisition_id: int) -> dict[str, Any]:
    """
    Execute the 8-node Final Ranking LangGraph for a single JobRequisition.

    Returns the final FinalRankingState dict.
    Check state.get("error") for pipeline-level failures.
    """
    logger.info("[final_ranking_runner] Starting for JR %d.", requisition_id)

    initial_state: FinalRankingState = {
        "requisition_id":    requisition_id,
        "posting_id":        None,
        "job_title":         None,
        "company_name":      None,
        "applications":      [],
        "has_assessment":    False,
        "has_tech_interview": False,
        "has_hr_interview":  False,
        "has_tech_analysis": False,
        "has_hr_analysis":   False,
        "weights":           {},
        "scored":            [],
        "rankings_persisted": False,
        "emails_sent":       0,
        "error":             None,
    }

    return _get_compiled_graph().invoke(initial_state)

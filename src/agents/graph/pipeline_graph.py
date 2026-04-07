"""
Master Recruitment Pipeline Graph
───────────────────────────────────
Full flow (phases activate sequentially as current_phase advances):

  [analyze_requisition]
         │
  [generate_posts]
         │
  [save_posts]  ──► current_phase = "cv_screening"
         │
  [screen_cvs]  ──► current_phase = "assessment"      (placeholder)
         │
  [run_assessment]  ──► current_phase = "technical_interview"  (placeholder)
         │
  [run_technical_interview]  ──► current_phase = "hr_interview"  (placeholder)
         │
  [run_hr_interview]  ──► current_phase = "final_ranking"  (placeholder)
         │
  [rank_candidates]  ──► current_phase = "completed"  (placeholder)
         │
        END

Any node can set current_phase = "error" to short-circuit to END.
"""

from langgraph.graph import StateGraph, END

from agents.state import PipelineState
from agents.nodes import (
    analyze_requisition,
    generate_posts,
    save_posts,
    screen_cvs,
    run_assessment,
    run_technical_interview,
    run_hr_interview,
    rank_candidates,
)


# ── Routing helpers ────────────────────────────────────────────────────────

def _route_after_save(state: PipelineState) -> str:
    """After save_posts: go to cv_screening or stop on error."""
    if state["current_phase"] == "error":
        return "error"
    return "cv_screening"


def _route_after_cv_screening(state: PipelineState) -> str:
    if state["current_phase"] == "error":
        return "error"
    if state["current_phase"] == "assessment":
        return "assessment"
    return "end"   # placeholder returns "completed"


def _route_after_assessment(state: PipelineState) -> str:
    if state["current_phase"] == "error":
        return "error"
    if state["current_phase"] == "technical_interview":
        return "technical_interview"
    return "end"


def _route_after_technical(state: PipelineState) -> str:
    if state["current_phase"] == "error":
        return "error"
    if state["current_phase"] == "hr_interview":
        return "hr_interview"
    return "end"


def _route_after_hr(state: PipelineState) -> str:
    if state["current_phase"] == "error":
        return "error"
    if state["current_phase"] == "final_ranking":
        return "final_ranking"
    return "end"


def _route_after_ranking(state: PipelineState) -> str:
    if state["current_phase"] == "error":
        return "error"
    return "end"


def _error_guard(state: PipelineState) -> str:
    """Generic error guard for nodes that don't advance phase themselves."""
    return "error" if state["current_phase"] == "error" else "continue"


# ── Graph builder ──────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    graph = StateGraph(PipelineState)

    # ── Register all nodes ─────────────────────────────────────────────────
    graph.add_node("analyze_requisition",    analyze_requisition)
    graph.add_node("generate_posts",         generate_posts)
    graph.add_node("save_posts",             save_posts)
    graph.add_node("screen_cvs",             screen_cvs)
    graph.add_node("run_assessment",         run_assessment)
    graph.add_node("run_technical_interview",run_technical_interview)
    graph.add_node("run_hr_interview",       run_hr_interview)
    graph.add_node("rank_candidates",        rank_candidates)

    # ── Entry ──────────────────────────────────────────────────────────────
    graph.set_entry_point("analyze_requisition")

    # ── Phase 1: Job Posting ───────────────────────────────────────────────
    # analyze → generate (stop on error)
    graph.add_conditional_edges(
        "analyze_requisition",
        _error_guard,
        {"continue": "generate_posts", "error": END},
    )
    # generate → save (stop on error)
    graph.add_conditional_edges(
        "generate_posts",
        _error_guard,
        {"continue": "save_posts", "error": END},
    )
    # save → cv_screening | error
    graph.add_conditional_edges(
        "save_posts",
        _route_after_save,
        {"cv_screening": "screen_cvs", "error": END},
    )

    # ── Phase 2: CV Screening ──────────────────────────────────────────────
    graph.add_conditional_edges(
        "screen_cvs",
        _route_after_cv_screening,
        {"assessment": "run_assessment", "end": END, "error": END},
    )

    # ── Phase 3: Assessment ────────────────────────────────────────────────
    graph.add_conditional_edges(
        "run_assessment",
        _route_after_assessment,
        {"technical_interview": "run_technical_interview", "end": END, "error": END},
    )

    # ── Phase 4: Technical Interview ───────────────────────────────────────
    graph.add_conditional_edges(
        "run_technical_interview",
        _route_after_technical,
        {"hr_interview": "run_hr_interview", "end": END, "error": END},
    )

    # ── Phase 5: HR Interview ──────────────────────────────────────────────
    graph.add_conditional_edges(
        "run_hr_interview",
        _route_after_hr,
        {"final_ranking": "rank_candidates", "end": END, "error": END},
    )

    # ── Phase 6: Final Ranking ─────────────────────────────────────────────
    graph.add_conditional_edges(
        "rank_candidates",
        _route_after_ranking,
        {"end": END, "error": END},
    )

    return graph.compile()

"""
Node 4 — sort_and_rank_node

Sorts scored candidates by (red_flag ASC, weighted_total_score DESC) and
assigns final_rank (1 = best).  Red-flagged candidates are always sorted to
the bottom regardless of their score.
"""

import logging

from graphs.states.final_ranking_state import FinalRankingState

logger = logging.getLogger(__name__)


def sort_and_rank_node(state: FinalRankingState) -> FinalRankingState:
    """Sort candidates and assign final_rank."""
    if state.get("error"):
        return state

    scored: list[dict] = state.get("scored", [])
    if not scored:
        return {**state, "error": "No scored candidates to rank."}

    scored.sort(key=lambda x: (x["red_flag"], -x["weighted_total_score"]))

    for rank, row in enumerate(scored, start=1):
        row["final_rank"] = rank
        row["final_recommendation"] = "Hire" if rank <= 5 else "No Hire"
        row["final_status"] = "Selected" if rank <= 5 else "Not Selected"

    top = scored[0] if scored else {}
    top_candidate = top.get("candidate")
    top_name = getattr(top_candidate, "first_name", None) or "—"
    logger.info(
        "[final_ranking:sort] Ranked %d candidates. Top: %s @ %.2f%s",
        len(scored),
        top_name,
        top.get("weighted_total_score", 0),
        " [RED FLAG]" if top.get("red_flag") else "",
    )
    return {**state, "scored": scored}

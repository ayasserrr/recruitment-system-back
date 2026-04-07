"""
Phase 6 — Final Ranking
────────────────────────
COMING SOON.

This node will:
  1. Collect all scores: semantic (CV), assessment, technical interview, HR interview.
  2. Apply weighted formula → weighted_total_score.
  3. Save results to final_rankings table.
  4. Generate final_recommendation per candidate (Hire / Reject / Waitlist).
  5. Advance current_phase → "completed".
"""

import logging
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def rank_candidates(state: PipelineState) -> PipelineState:
    """Phase 6 placeholder — Weighted final ranking of all candidates."""
    logger.info(
        f"[rank_candidates] Phase 6 (Final Ranking) reached for requisition "
        f"{state['requisition_id']}. Implementation pending."
    )
    return {**state, "current_phase": "completed"}

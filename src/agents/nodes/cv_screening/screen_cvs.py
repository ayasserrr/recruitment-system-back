"""
Phase 2 — CV Screening
──────────────────────
COMING SOON.

This node will:
  1. Fetch all Applications for the posting.
  2. Run semantic similarity between each CV and the JR requirements.
  3. Score, rank, and save results to semantic_analysis_reports.
  4. Update application.current_pipeline_stage = "cv_screening".
  5. Advance current_phase → "assessment".
"""

import logging
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def screen_cvs(state: PipelineState) -> PipelineState:
    """Phase 2 placeholder — CV semantic screening."""
    logger.info(
        f"[screen_cvs] Phase 2 (CV Screening) reached for requisition "
        f"{state['requisition_id']}. Implementation pending."
    )
    # When implemented, set current_phase = "assessment"
    return {**state, "current_phase": "completed"}

"""
Phase 5 — HR Interview
──────────────────────
COMING SOON.

This node will:
  1. Fetch candidates who passed the technical interview.
  2. Run AI-conducted HR interviews (questions from hr_interview_criteria / culture values).
  3. Score and save to hr_interview_sessions + hr_interview_reports.
  4. Update application.current_pipeline_stage = "hr_interview".
  5. Advance current_phase → "final_ranking".
"""

import logging
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def run_hr_interview(state: PipelineState) -> PipelineState:
    """Phase 5 placeholder — AI-conducted HR interview."""
    logger.info(
        f"[run_hr_interview] Phase 5 (HR Interview) reached for requisition "
        f"{state['requisition_id']}. Implementation pending."
    )
    return {**state, "current_phase": "completed"}

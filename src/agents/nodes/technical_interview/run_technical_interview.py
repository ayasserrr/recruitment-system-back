"""
Phase 4 — Technical Interview
──────────────────────────────
COMING SOON.

This node will:
  1. Fetch candidates who passed the assessment.
  2. Run AI-conducted technical interviews (questions from interview_evaluation_criteria).
  3. Score and save to technical_interview_sessions + technical_interview_reports.
  4. Update application.current_pipeline_stage = "technical_interview".
  5. Advance current_phase → "hr_interview".
"""

import logging
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def run_technical_interview(state: PipelineState) -> PipelineState:
    """Phase 4 placeholder — AI-conducted technical interview."""
    logger.info(
        f"[run_technical_interview] Phase 4 (Technical Interview) reached for requisition "
        f"{state['requisition_id']}. Implementation pending."
    )
    return {**state, "current_phase": "completed"}

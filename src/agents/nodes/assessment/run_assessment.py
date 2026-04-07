"""
Phase 3 — Technical Assessment
───────────────────────────────
COMING SOON.

This node will:
  1. Fetch candidates who passed CV screening.
  2. Send assessment invites / run AI-generated questions.
  3. Score answers and save to candidate_assessments + assessment_reports.
  4. Update application.current_pipeline_stage = "assessment".
  5. Advance current_phase → "technical_interview".
"""

import logging
from agents.state import PipelineState

logger = logging.getLogger(__name__)


def run_assessment(state: PipelineState) -> PipelineState:
    """Phase 3 placeholder — Technical assessment scoring."""
    logger.info(
        f"[run_assessment] Phase 3 (Assessment) reached for requisition "
        f"{state['requisition_id']}. Implementation pending."
    )
    return {**state, "current_phase": "completed"}

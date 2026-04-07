"""
agents/runner.py
─────────────────
Public entry point for the entire recruitment pipeline.

Usage (from any controller):
    from agents.runner import trigger_pipeline
    trigger_pipeline(requisition_id=4)
"""

import logging
import threading

from agents.graph import build_pipeline
from agents.state import PipelineState

logger   = logging.getLogger(__name__)
_pipeline = build_pipeline()


def _run(requisition_id: int) -> None:
    """Runs the full pipeline graph inside a background thread."""
    logger.info(f"[Pipeline] Starting for requisition {requisition_id}")
    try:
        initial_state: PipelineState = {
            "requisition_id":           requisition_id,
            "posting_id":               None,
            "current_phase":            "job_posting",
            "jr_data":                  {},
            "generated_posts":          {},
            "screened_applications":    [],
            "assessment_results":       [],
            "technical_results":        [],
            "hr_results":               [],
            "final_rankings":           [],
            "error":                    None,
        }

        final_state = _pipeline.invoke(initial_state)

        if final_state["current_phase"] == "error":
            logger.error(
                f"[Pipeline] Failed at phase for requisition {requisition_id}: "
                f"{final_state.get('error')}"
            )
        else:
            logger.info(
                f"[Pipeline] Completed for requisition {requisition_id} "
                f"— final phase: {final_state['current_phase']}"
            )
    except Exception as e:
        logger.error(f"[Pipeline] Unhandled exception for requisition {requisition_id}: {e}")


def trigger_pipeline(requisition_id: int) -> None:
    """
    Fire-and-forget: launches the full recruitment pipeline in a
    background daemon thread. Returns immediately — never blocks the API.
    """
    thread = threading.Thread(
        target=_run,
        args=(requisition_id,),
        daemon=True,
        name=f"pipeline-{requisition_id}",
    )
    thread.start()
    logger.info(f"[Pipeline] Background thread launched for requisition {requisition_id}")

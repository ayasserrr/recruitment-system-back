"""
Phase 2 — CV Screening
──────────────────────
Executes the full 6-node LangGraph ranking workflow for all applications
attached to the current requisition, then advances the pipeline to Phase 3.

Workflow (delegated to services/ranking_graph.py):
  1. context_gatherer_node      — load JD + applications + CV data from DB
  2. deterministic_scoring_node — rule-based scoring (skills, experience, education…)
  3. llm_qualitative_node       — GPT-4o-mini project depth + strengths/concerns
  4. genai_validator_node        — GenAI evidence + deployment context bonus
  5. final_ranker_node           — weighted final score + pool-relative label
  6. persistence_node            — upsert semantic_analysis_reports + matched skills
"""

import logging

from agents.state import PipelineState
from services.ranking_graph import run_ranking_graph

logger = logging.getLogger(__name__)


def screen_cvs(state: PipelineState) -> PipelineState:
    """Phase 2 — Run the full CV ranking workflow for the current requisition."""
    req_id = state["requisition_id"]
    logger.info("[screen_cvs] Starting CV screening for requisition %d.", req_id)

    try:
        result = run_ranking_graph(req_id)

        if result.get("error"):
            logger.error(
                "[screen_cvs] Ranking workflow returned an error for requisition %d: %s",
                req_id, result["error"],
            )
            return {
                **state,
                "current_phase": "assessment",
                "screened_applications": [],
                "screening_error": result["error"],
            }

        ranked = result.get("ranked_candidates", [])
        screened = [
            {
                "application_id": c.get("application_id"),
                "candidate_name": c.get("candidate_name"),
                "final_score": c.get("final_score"),
                "rank_in_pool": c.get("rank_in_pool"),
                "recommendation": c.get("recommendation"),
            }
            for c in ranked
        ]

        logger.info(
            "[screen_cvs] Screening complete for requisition %d — "
            "%d candidates ranked. Advancing to assessment phase.",
            req_id, len(screened),
        )

        return {
            **state,
            "current_phase": "assessment",
            "screened_applications": screened,
        }

    except Exception as exc:
        logger.exception(
            "[screen_cvs] Unhandled error during CV screening for requisition %d.", req_id
        )
        return {
            **state,
            "current_phase": "assessment",
            "screened_applications": [],
            "screening_error": str(exc),
        }

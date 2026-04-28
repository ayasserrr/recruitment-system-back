"""
Ranking Service — public facade for the 4-Stage Final Ranking pipeline.

The actual logic lives in the LangGraph nodes under:
    src/graphs/nodes/final_ranking/

Stage weights:
    Screening (CV Match)         20%
    Technical Assessment         25%
    Technical Interview          30%
    HR Interview (Soft Skills)   25%

Weights redistribute proportionally when any stage has no data.

Usage from Celery tasks or route handlers:
    from services.ranking_service import compute_final_rankings

    result = compute_final_rankings(requisition_id=42)
    if result.get("error"):
        logger.error("Ranking failed: %s", result["error"])
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def compute_final_rankings(requisition_id: int) -> dict[str, Any]:
    """
    Trigger the 8-node Final Ranking LangGraph for a single JobRequisition.

    Pipeline stages (in order):
      1. gather_applications  — load Shortlisted candidates
      2. compute_weights      — determine effective 20/25/30/25 weights
      3. tech_interview       — CodeBERT + RoBERTa-QA ensemble on tech transcripts
      4. hr_analysis          — Go-Emotions + Sentiment + NLI ensemble on HR transcripts
      5. score_candidates     — 4-component weighted total + cross-phase SHAP
      6. sort_and_rank        — sort by weighted_total (red-flagged to bottom)
      7. persist_rankings     — upsert FinalRanking rows + shap columns
      8. send_decisions       — email hire / no-hire to all candidates

    Returns:
        The final FinalRankingState dict.
        Check result.get("error") for pipeline-level failures.
    """
    from graphs.runners.final_ranking_runner import run_final_ranking
    logger.info("[ranking_service] Starting final ranking for JR %d.", requisition_id)
    return run_final_ranking(requisition_id)

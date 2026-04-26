"""
State for the CV Ranking LangGraph workflow.

Nodes (in order):
  context_gatherer       → loads JD + all CVs from DB
  deterministic_scorer   → rule-based scoring (experience / education / skills)
  llm_qualitative        → GPT-4o-mini: project depth, strengths, concerns
  genai_validator        → GenAI evidence + deployment context
  final_ranker           → pool-relative labels + rank_in_pool assignment
  persistence            → upserts semantic_analysis_reports + matched skills
"""

from typing import Optional
from typing_extensions import TypedDict


class RankingState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]

    # Loaded by context_gatherer
    jd_data: dict            # JD fields + required skills list

    # Enriched candidate records flowing through the pipeline
    candidates_data: list    # raw DB records (CV, experience, education, skills)
    scored_candidates: list  # + det_scores, matched_skills, applied_fixes
    llm_results: list        # + llm_scores (project_depth, strengths, concerns)
    validated_candidates: list  # + genai_data (evidence, context, bonus)
    ranked_candidates: list  # + final_score, rank_in_pool, recommendation

    # Error sentinel — any node can set this; all subsequent nodes short-circuit
    error: Optional[str]

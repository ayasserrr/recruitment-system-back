"""
State for the Auto-Grading LangGraph workflow.

Nodes (in order):
  load_submission      → fetch CandidateAssessment + answers from DB
  keyword_coverage     → Phase 1: keyword match with GPT-4o-mini semantic fallback
  depth_scoring        → Phase 2: comparative depth scoring (normalised vs pool max)
  save_results         → persist AssessmentLeaderboard entry
"""

from typing import Optional
from typing_extensions import TypedDict


class GradingState(TypedDict):
    assessment_id: int
    requisition_id: int
    candidate_id: int
    application_id: int

    # Loaded by load_submission
    answers: list[dict]      # [{question_id, answer_text, max_points}]
    keywords: list[str]      # required keywords from AssessmentTemplateQuestion
    pool_scores: list[float] # all submitted total_scores in the pool (for normalisation)

    # Set by keyword_coverage
    keyword_score: float     # 0.0 – 1.0

    # Set by depth_scoring
    depth_score: float       # 0.0 – 1.0
    final_score: float       # normalised composite

    # Error sentinel
    error: Optional[str]

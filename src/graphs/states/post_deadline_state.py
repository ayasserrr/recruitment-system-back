"""
State for the Post-Deadline Assessment LangGraph workflow.

Nodes (in order):
  load_pool          → fetch all CandidateAssessments for the requisition
  mark_no_shows      → Pending / In-Progress → No-show, passed = False
  rank_pool          → sort Submitted by total_score, assign rank_in_pool
  generate_report    → GPT-4o-mini pool summary → TechnicalAssessmentConfig.pool_report
"""

from typing import Optional
from typing_extensions import TypedDict


class PostDeadlineState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]
    config_id: Optional[int]

    # Loaded by load_pool
    assessments: list[dict]  # {assessment_id, status, total_score, candidate_name}

    # Set by mark_no_shows
    no_show_count: int

    # Set by rank_pool
    ranked_count: int

    # Set by generate_report
    pool_report: Optional[str]

    # Error sentinel
    error: Optional[str]

"""
State for the Final Ranking LangGraph workflow.

Nodes (in order):
  gather_applications  → load all Shortlisted applications + their scores
  compute_weights      → determine effective CV / assessment / interview weights
  score_candidates     → weighted total + red-flag detection per candidate
  sort_and_rank        → sort by weighted_total (red-flagged to bottom), assign rank
  persist_rankings     → upsert FinalRanking rows, set jr.status = 'ranking_complete'
  send_decisions       → email hire / no-hire to all candidates
"""

from typing import Optional
from typing_extensions import TypedDict


class FinalRankingState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]
    job_title: Optional[str]
    company_name: Optional[str]

    # Set by gather_applications
    applications: list[dict]   # [{application_id, posting_id, candidate obj}]

    # Set by compute_weights
    has_assessment: bool
    has_interview: bool
    weights: dict              # {"cv": float, "assessment": float, "interview": float}

    # Set by score_candidates
    scored: list[dict]         # [{application_id, cv_score, assessment_score,
                               #   interview_score, weighted_total_score,
                               #   red_flag, red_flag_reason, candidate}]

    # Set by persist_rankings
    rankings_persisted: bool

    # Set by send_decisions
    emails_sent: int

    # Error sentinel
    error: Optional[str]

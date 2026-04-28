"""
State for the Final Ranking LangGraph workflow.

4-Stage Pipeline (in order):
  gather_applications  → load all Shortlisted applications
  compute_weights      → determine effective stage weights
  tech_interview       → CodeBERT + RoBERTa-QA ensemble on technical transcripts
  hr_analysis          → Go-Emotions + Sentiment + NLI ensemble on HR transcripts
  score_candidates     → 4-component weighted total + cross-phase SHAP + red-flags
  sort_and_rank        → sort by weighted_total (red-flagged to bottom), assign rank
  persist_rankings     → upsert FinalRanking rows, JR.status = 'ranking_complete'
  send_decisions       → email hire / no-hire to all candidates

Default weights:
    Screening (CV Match)       20%
    Technical Assessment       25%
    Technical Interview        30%
    HR Interview (Soft Skills) 25%
"""

from typing import Optional
from typing_extensions import TypedDict


class FinalRankingState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]
    job_title: Optional[str]
    company_name: Optional[str]

    # Set by gather_applications
    applications: list[dict]       # [{application_id, posting_id, candidate obj}]

    # Set by compute_weights
    has_assessment:    bool
    has_tech_interview: bool       # True when ≥1 Completed TechnicalInterviewSession
    has_hr_interview:  bool        # True when ≥1 Completed HRInterviewSession
    weights: dict                  # {"cv", "assessment", "tech_interview", "hr_interview"}

    # Set by tech_interview_node
    has_tech_analysis: bool        # True when ensemble scores were computed / already present

    # Set by hr_analysis_node
    has_hr_analysis: bool          # True when ensemble scores were computed / already present

    # Set by score_candidates
    scored: list[dict]             # [{application_id, semantic_score, assessment_score,
                                   #   technical_interview_score, hr_interview_score,
                                   #   weighted_total_score, shap_json, shap_summary,
                                   #   red_flag, red_flag_reason, candidate}]

    # Set by persist_rankings
    rankings_persisted: bool

    # Set by send_decisions
    emails_sent: int

    # Error sentinel
    error: Optional[str]

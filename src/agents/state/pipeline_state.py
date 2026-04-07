"""
PipelineState — the single shared state that flows through every node
in the recruitment pipeline graph.

Phases (in order):
  1. job_posting        → generate & save LinkedIn / Indeed posts
  2. cv_screening       → semantic analysis of candidate CVs          [future]
  3. assessment         → technical assessment scoring                 [future]
  4. technical_interview→ AI-conducted technical interview             [future]
  5. hr_interview       → AI-conducted HR interview                    [future]
  6. final_ranking      → weighted score + final recommendation        [future]
"""

from typing import TypedDict, Optional


class PipelineState(TypedDict):
    # ── Identity ──────────────────────────────────────────────────────────
    requisition_id: int
    posting_id: Optional[int]      # populated after job_posting phase

    # ── Phase control ─────────────────────────────────────────────────────
    current_phase: str
    # Values: "job_posting" | "cv_screening" | "assessment" |
    #         "technical_interview" | "hr_interview" | "final_ranking" |
    #         "completed" | "error"

    # ── Phase 1: job_posting data ─────────────────────────────────────────
    jr_data: dict           # job requisition fields extracted from DB
    generated_posts: dict   # {"linkedin": {title, content}, "indeed": {title, content}}

    # ── Phase 2: cv_screening data ────────────────────────────────────────
    screened_applications: list   # [{"application_id", "match_percentage", ...}]

    # ── Phase 3: assessment data ──────────────────────────────────────────
    assessment_results: list      # [{"application_id", "score", ...}]

    # ── Phase 4: technical_interview data ────────────────────────────────
    technical_results: list       # [{"application_id", "score", ...}]

    # ── Phase 5: hr_interview data ────────────────────────────────────────
    hr_results: list              # [{"application_id", "score", ...}]

    # ── Phase 6: final_ranking data ───────────────────────────────────────
    final_rankings: list          # [{"application_id", "rank", "recommendation"}]

    # ── Error handling ────────────────────────────────────────────────────
    error: Optional[str]

"""
State for the Technical Assessment LangGraph workflow.

Nodes (in order):
  load_context        → fetch JR, config, skills, shortlisted candidates
  generate_questions  → GPT-4o-mini → AssessmentTemplateQuestion rows
  create_assessments  → one CandidateAssessment per shortlisted candidate
  send_invitations    → HMAC-signed email per candidate
"""

from typing import Optional
from typing_extensions import TypedDict


class AssessmentState(TypedDict):
    requisition_id: int
    company_id: int
    job_title: str
    seniority: str
    config_id: int
    template_id: Optional[int]

    # Loaded by load_context
    skills: list[dict]         # [{"skill_name": str, "skill_type": str}]
    shortlisted: list[dict]    # [{"application_id", "candidate_id", "email", "first_name"}]

    # Set by generate_questions
    questions_generated: bool

    # Set by create_assessments
    assessment_ids: list[int]

    # Error sentinel
    error: Optional[str]

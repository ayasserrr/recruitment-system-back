"""
Fetches all data needed for the relative grading pipeline for a given jr_id.

Concept chain:
  generated_assessment_questions (jr_id, required_keywords, question_text)
  └── template_question_id → assessment_template_questions.question_id
       └── assessment_answers.question_id (candidate answer)

Candidate chain:
  job_requisitions → job_postings → applications → candidate_assessments
  → assessment_answers (keyed by template_question_id)
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from models.db.application import Application
from models.db.assessment_answer import AssessmentAnswer
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.generated_assessment_question import GeneratedAssessmentQuestion
from models.db.job_posting import JobPosting

logger = logging.getLogger(__name__)


def load_concepts(jr_id: int, db: Session) -> list[dict]:
    """
    Returns one dict per active generated question for the given JR.

    Each dict contains:
      concept_name, tool_name, level, question_text,
      required_keywords (list[str]), template_question_id
    """
    questions = (
        db.query(GeneratedAssessmentQuestion)
        .filter(
            GeneratedAssessmentQuestion.jr_id == jr_id,
            GeneratedAssessmentQuestion.is_active == True,
            GeneratedAssessmentQuestion.template_question_id.isnot(None),
        )
        .all()
    )

    concepts: list[dict] = []
    for q in questions:
        kws = q.required_keywords
        if not isinstance(kws, list) or not kws:
            logger.warning(
                "[data_loader] concept '%s' (jr=%d) has no required_keywords — skipping.",
                q.concept_name, jr_id,
            )
            continue
        concepts.append({
            "concept_name": q.concept_name,
            "tool_name": q.tool_name,
            "level": q.level,
            "question_text": q.question_text,
            "required_keywords": [str(k) for k in kws],
            "template_question_id": q.template_question_id,
            "generated_question_id": q.id,
            # concept_id is NULL for external knowledge questions (no DB match)
            "is_external": q.concept_id is None,
        })

    logger.info(
        "[data_loader] Loaded %d concepts with keywords for jr_id=%d.",
        len(concepts), jr_id,
    )
    return concepts


def load_candidates(jr_id: int, db: Session) -> list[dict]:
    """
    Returns one dict per candidate who submitted an assessment for this JR.

    Each dict contains:
      name, candidate_id, application_id, assessment_id,
      answers: {template_question_id (int) → candidate_answer (str)}
    """
    posting: Optional[JobPosting] = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == jr_id)
        .first()
    )
    if not posting:
        logger.warning("[data_loader] No posting found for jr_id=%d.", jr_id)
        return []

    rows = (
        db.query(CandidateAssessment, Application, Candidate)
        .join(Application, CandidateAssessment.application_id == Application.application_id)
        .join(Candidate, Application.candidate_id == Candidate.candidate_id)
        .filter(
            Application.posting_id == posting.posting_id,
            CandidateAssessment.status == "Submitted",
        )
        .all()
    )

    candidates: list[dict] = []
    for assessment, app, candidate in rows:
        answer_rows = (
            db.query(AssessmentAnswer)
            .filter(AssessmentAnswer.assessment_id == assessment.assessment_id)
            .all()
        )
        # Map: template_question_id (int) → answer text
        answers: dict[int, str] = {
            a.question_id: (a.candidate_answer or "")
            for a in answer_rows
        }
        candidates.append({
            "name": f"{candidate.first_name} {candidate.last_name}".strip(),
            "candidate_id": candidate.candidate_id,
            "application_id": app.application_id,
            "assessment_id": assessment.assessment_id,
            "answers": answers,
        })

    logger.info(
        "[data_loader] Loaded %d submitted candidates for jr_id=%d.",
        len(candidates), jr_id,
    )
    return candidates

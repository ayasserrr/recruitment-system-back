from typing import Optional
from pydantic import BaseModel, Field


class AnswerSubmission(BaseModel):
    question_id: int
    candidate_answer: str = Field(..., description="Candidate's answer (option letter for MCQ, free text for open-ended)")


class AssessmentSubmitRequest(BaseModel):
    token: str = Field(..., description="HMAC-signed security token from the invitation link")
    answers: list[AnswerSubmission] = Field(..., min_length=1, description="List of question-answer pairs")


class AnswerResult(BaseModel):
    question_id: int
    question_text: str
    question_type: str
    candidate_answer: str
    score_awarded: Optional[float]   # None for open-ended (graded post-deadline)
    max_points: int
    ai_feedback: str


class AssessmentSubmitResponse(BaseModel):
    assessment_id: int
    status: str
    total_score: float               # MCQ-only until post-deadline grading runs
    passing_score: Optional[float]
    passed: Optional[bool]           # None until open-ended scores are available
    answers: list[AnswerResult]
    report: Optional["AssessmentReportResponse"]
    grading_note: Optional[str] = None  # explains deferred open-ended grading


class AssessmentReportResponse(BaseModel):
    report_id: int
    overall_score: float
    strengths: str
    weaknesses: str
    ai_feedback: str
    recommendation: str


AssessmentSubmitResponse.model_rebuild()

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
    score_awarded: float
    max_points: int
    ai_feedback: str


class AssessmentSubmitResponse(BaseModel):
    assessment_id: int
    status: str
    total_score: float
    passing_score: Optional[float]
    passed: Optional[bool]
    answers: list[AnswerResult]
    report: Optional["AssessmentReportResponse"]


class AssessmentReportResponse(BaseModel):
    report_id: int
    overall_score: float
    strengths: str
    weaknesses: str
    ai_feedback: str
    recommendation: str


AssessmentSubmitResponse.model_rebuild()

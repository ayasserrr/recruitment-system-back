"""
Shared dataclasses and enums for the interview agent system.
Extracted here to break the circular import between
technical_interview_agent.py and the persona files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class InterviewMode(Enum):
    TECHNICAL = "technical"
    HR = "hr"


@dataclass
class InterviewContext:
    candidate_id: str
    application_id: int
    match_percentage: float
    hr_explanation_json: Dict[str, Any]
    gaps: List[str]
    strengths: List[str]
    screening_score: float


@dataclass
class QuestionResponse:
    question_id: str
    question_text: str
    question_type: str
    audio_url: Optional[str]
    response_text: str
    response_audio_url: Optional[str]
    timestamp: datetime
    centroid_score: float
    depth_boost: float
    llm_qualitative_score: float
    confidence_score: float


@dataclass
class InterviewSession:
    session_id: str
    candidate_id: str
    application_id: int
    mode: InterviewMode
    context: InterviewContext
    questions_asked: List[QuestionResponse]
    final_score: float
    transcript: str
    audio_recording_url: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]

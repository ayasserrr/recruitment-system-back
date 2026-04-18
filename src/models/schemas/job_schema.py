import re
from pydantic import BaseModel, field_validator
from typing import List, Optional
from datetime import date


def _sanitize_skill_list(values: list) -> list[str]:
    """
    Normalize a list of skill strings:
      - Strip surrounding whitespace
      - Drop None, empty, or whitespace-only entries
      - Drop entries shorter than 2 non-whitespace characters (e.g. "i", "g.")
      - Drop entries that contain only punctuation / digits
      - Deduplicate (case-insensitive, keeps first occurrence)
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in (values or []):
        if not raw:
            continue
        skill = str(raw).strip()
        # Must have at least 2 word characters (letters/digits/underscore)
        if len(re.sub(r"[^\w]", "", skill)) < 2:
            continue
        # Deduplicate case-insensitively
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(skill)
    return cleaned


class JobRequisitionCreate(BaseModel):
    # ── Basic Info ──────────────────────────────────────────────────────────
    jobTitle: str
    department: Optional[str] = None
    seniorityLevel: Optional[str] = None
    employmentType: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    remoteAvailable: bool = False

    # ── Requirements ────────────────────────────────────────────────────────
    requiredSkills: List[Optional[str]] = []
    preferredSkills: List[Optional[str]] = []

    @field_validator("requiredSkills", "preferredSkills", mode="before")
    @classmethod
    def sanitize_skills(cls, v):
        return _sanitize_skill_list(v if isinstance(v, list) else [])
    minExperience: Optional[int] = None
    maxExperience: Optional[int] = None
    minEducation: Optional[str] = None
    fieldOfStudy: Optional[str] = None
    # Each entry may carry proficiency: "English (Fluent)", "Spanish (Basic)"
    languages: List[Optional[str]] = []

    # ── Description ─────────────────────────────────────────────────────────
    # Frontend sends a list; stored as newline-joined text
    responsibilities: List[Optional[str]] = []
    fullDescription: Optional[str] = None

    # ── Compensation / Posting dates ────────────────────────────────────────
    currency: Optional[str] = None
    minSalary: Optional[float] = None
    maxSalary: Optional[float] = None
    deadline: Optional[date] = None
    contactEmail: Optional[str] = None
    additionalNotes: Optional[str] = None
    postingStartDate: Optional[date] = None
    postingEndDate: Optional[date] = None          # → cv_collection_end_date

    # ── Platforms ───────────────────────────────────────────────────────────
    selectedPlatforms: List[Optional[str]] = []

    # ── Technical Assessment ────────────────────────────────────────────────
    assessmentType: Optional[str] = None           # "ai-generated" | "custom"
    assessmentQuestions: List[Optional[str]] = []  # custom questions (serialised → sample_task)
    assessmentTimeLimit: Optional[int] = None
    assessmentCandidatesToAdvance: Optional[int] = None
    maxAttempts: Optional[str] = "1"               # arrives as string
    assessmentLanguage: Optional[str] = None
    templateTask: Optional[str] = None             # → sample_task (custom type)

    # ── Technical Interview ─────────────────────────────────────────────────
    technicalInterviewType: Optional[str] = None
    technicalInterviewQuestions: List[Optional[str]] = []
    aiEvaluationCriteria: List[Optional[str]] = []
    aiFeedbackLevel: Optional[str] = None
    numberOfInterviewers: Optional[str] = None
    scoringSystem: Optional[str] = None
    technicalInterviewDuration: Optional[str] = None   # arrives as string
    technicalInterviewCandidatesToAdvance: Optional[int] = None
    interviewerNotes: Optional[str] = None

    # ── HR Interview ────────────────────────────────────────────────────────
    hrInterviewType: Optional[str] = None
    hrInterviewQuestions: List[Optional[str]] = []
    hrEvaluationCriteria: List[Optional[str]] = []
    cultureValues: Optional[str] = None
    hrInterviewDuration: Optional[str] = None      # arrives as string
    hrInterviewFormat: Optional[str] = None
    hrInterviewCandidatesToAdvance: Optional[int] = None
    hrDecisionTimeline: Optional[str] = None
    hrInterviewerNotes: Optional[str] = None

    # ── Coerce numeric-string fields ────────────────────────────────────────
    @field_validator("maxAttempts", "technicalInterviewDuration", "hrInterviewDuration", mode="before")
    @classmethod
    def coerce_to_str(cls, v):
        return str(v) if v is not None else None


class JobRequisitionResponse(BaseModel):
    requisition_id: int
    job_title: str
    department: Optional[str] = None
    status: str

    class Config:
        from_attributes = True

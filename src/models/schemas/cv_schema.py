from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, field_validator


class WorkExperienceSchema(BaseModel):
    job_title: Optional[str] = None
    company: Optional[str] = None
    start_date: Optional[str] = None   # YYYY-MM-DD
    end_date: Optional[str] = None     # YYYY-MM-DD
    responsibilities: Optional[List[str]] = None

    @field_validator("job_title", "company", "start_date", "end_date", mode="before")
    @classmethod
    def empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("responsibilities", mode="before")
    @classmethod
    def coerce_responsibilities(cls, v: object) -> object:
        if isinstance(v, str):
            return [v] if v.strip() else None
        return v


class EducationSchema(BaseModel):
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    graduation_date: Optional[str] = None   # YYYY-MM-DD

    @field_validator("institution", "degree", "field", "graduation_date", mode="before")
    @classmethod
    def empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ProjectSchema(BaseModel):
    project_name: Optional[str] = None
    description: Optional[str] = None
    tech_stack: Optional[List[str]] = None

    @field_validator("project_name", "description", mode="before")
    @classmethod
    def empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("tech_stack", mode="before")
    @classmethod
    def coerce_tech_stack(cls, v: object) -> object:
        if isinstance(v, str):
            parts = [s.strip() for s in v.split(",") if s.strip()]
            return parts if parts else None
        return v


class SkillSchema(BaseModel):
    skill_name: str   # NOT NULL in cv_skills table
    proficiency_level: Optional[str] = None

    @field_validator("skill_name")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("skill_name must not be empty")
        return v.strip()

    @field_validator("proficiency_level", mode="before")
    @classmethod
    def empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ParsedCVSchema(BaseModel):
    # Mandatory — NOT NULL in candidates table
    first_name: str
    last_name: str
    email: str

    # Optional candidate profile fields
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    professional_summary: Optional[str] = None
    years_of_experience: Optional[int] = None
    education_level: Optional[str] = None
    field_of_study: Optional[str] = None

    # Nested CV sections
    experiences: List[WorkExperienceSchema] = []
    educations: List[EducationSchema] = []
    projects: List[ProjectSchema] = []
    skills: List[SkillSchema] = []

    @field_validator("first_name", "last_name", "email", mode="before")
    @classmethod
    def must_not_be_empty(cls, v: object) -> str:
        if not v or (isinstance(v, str) and not v.strip()):
            raise ValueError("must not be empty")
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()

    @field_validator(
        "phone", "linkedin_url", "portfolio_url", "professional_summary",
        "education_level", "field_of_study",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("years_of_experience", mode="before")
    @classmethod
    def parse_years(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        match = re.search(r"\d+", str(v))
        return int(match.group()) if match else None

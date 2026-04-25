"""
CV / JD Pydantic V2 Schemas
────────────────────────────
Single source of truth for all structured CV and JD data flowing through
the recruitment pipeline.  Every node in ranking_graph.py that reads or
writes candidate data must validate at the system boundary using these models.

Why Pydantic v2 over @dataclass:
  • Field validation at parse time — bad LLM output raises immediately.
  • .model_dump() is type-safe and replaces ad-hoc asdict() calls.
  • JSON Schema auto-generation for documentation / OpenAPI integration.
  • Coercive validators handle common LLM quirks (e.g. "3 years" as int).

Usage:
    from schemas.cv_schema import ParsedCV, ParsedJD

    # At LLM parse boundary:
    cv = ParsedCV.model_validate(llm_json_output)   # raises on bad data
    db_dict = cv.model_dump()                        # safe dict for DB writes

    # At DB read boundary:
    cv = ParsedCV.model_validate(db_row_dict)
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────

class SkillEntry(BaseModel):
    """One row from the CV skills section."""
    skill_name:        str
    proficiency_level: Optional[str] = None

    @field_validator("skill_name")
    @classmethod
    def skill_name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("skill_name must not be blank")
        return v

    @field_validator("proficiency_level", mode="before")
    @classmethod
    def coerce_proficiency(cls, v: Any) -> Optional[str]:
        if v is None or str(v).strip().lower() in ("null", "none", ""):
            return None
        return str(v).strip()


class ExperienceEntry(BaseModel):
    """One role in the CV work history."""
    job_title:        Optional[str] = None
    company:          Optional[str] = None
    start_date:       Optional[str] = None   # YYYY-MM-DD or null
    end_date:         Optional[str] = None   # YYYY-MM-DD, "present", or null
    responsibilities: list[str] = Field(default_factory=list)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def coerce_date(cls, v: Any) -> Optional[str]:
        if v is None or str(v).strip().lower() in ("null", "none", ""):
            return None
        return str(v).strip()

    @field_validator("responsibilities", mode="before")
    @classmethod
    def coerce_responsibilities(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            # LLM sometimes returns a single string instead of a list
            return [v] if v.strip() else []
        return [str(item).strip() for item in v if item]


class EducationEntry(BaseModel):
    """One education record."""
    institution:     Optional[str] = None
    degree:          Optional[str] = None
    field:           Optional[str] = None
    graduation_date: Optional[str] = None

    @field_validator("graduation_date", mode="before")
    @classmethod
    def coerce_graduation(cls, v: Any) -> Optional[str]:
        if v is None or str(v).strip().lower() in ("null", "none", ""):
            return None
        return str(v).strip()


class ProjectEntry(BaseModel):
    """One project entry."""
    project_name: Optional[str] = None
    description:  Optional[str] = None
    tech_stack:   list[str] = Field(default_factory=list)

    @field_validator("tech_stack", mode="before")
    @classmethod
    def coerce_tech_stack(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            # Accept comma-separated string from some LLM outputs
            return [t.strip() for t in v.split(",") if t.strip()]
        return [str(t).strip() for t in v if t]


# ─────────────────────────────────────────────────────────────────────────────
# Core CV schema
# ─────────────────────────────────────────────────────────────────────────────

class ParsedCV(BaseModel):
    """
    Parsed candidate CV — validated at every LLM/DB boundary.

    Required fields (NOT NULL in DB): first_name, last_name, email.
    All other fields are optional — set to None / [] when absent.
    """
    first_name:           str
    last_name:            str
    email:                str
    phone:                Optional[str] = None
    linkedin_url:         Optional[str] = None
    portfolio_url:        Optional[str] = None
    professional_summary: Optional[str] = None
    years_of_experience:  int = Field(default=0, ge=0, description="Total YoE as integer >= 0")
    education_level:      Optional[str] = None
    field_of_study:       Optional[str] = None
    experiences:          list[ExperienceEntry] = Field(default_factory=list)
    educations:           list[EducationEntry]  = Field(default_factory=list)
    projects:             list[ProjectEntry]    = Field(default_factory=list)
    skills:               list[SkillEntry]      = Field(default_factory=list)

    # ── Required field validators ─────────────────────────────────────────────

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"Invalid email address: {v!r}")
        return v

    @field_validator("first_name", "last_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name fields must not be blank")
        return v

    # ── Type coercions for common LLM output quirks ───────────────────────────

    @field_validator("years_of_experience", mode="before")
    @classmethod
    def coerce_yoe(cls, v: Any) -> int:
        """
        LLM outputs 'years_of_experience' as "3 years", 3.5, None, or "null".
        All are coerced to a non-negative integer.
        """
        if v is None:
            return 0
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("null", "none", ""):
                return 0
            m = re.search(r"\d+", v)
            return int(m.group()) if m else 0
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return 0

    @field_validator("phone", "linkedin_url", "portfolio_url",
                     "professional_summary", "education_level", "field_of_study",
                     mode="before")
    @classmethod
    def coerce_optional_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return None if s.lower() in ("null", "none", "") else s

    # ── Cross-field validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def deduplicate_skills(self) -> "ParsedCV":
        """Remove duplicate skill_name entries (case-insensitive)."""
        seen: set[str] = set()
        unique: list[SkillEntry] = []
        for sk in self.skills:
            key = sk.skill_name.lower()
            if key not in seen:
                seen.add(key)
                unique.append(sk)
        if len(unique) < len(self.skills):
            logger.debug(
                "[ParsedCV] Removed %d duplicate skills for %s %s",
                len(self.skills) - len(unique), self.first_name, self.last_name,
            )
        self.skills = unique
        return self

    # ── Convenience methods ───────────────────────────────────────────────────

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def skill_names(self) -> list[str]:
        """Return a flat list of normalized skill name strings."""
        return [sk.skill_name for sk in self.skills]

    def to_db_dict(self) -> dict:
        """
        Safe dict for DB writes — replaces asdict().
        Excludes sub-models (experiences, educations, projects, skills)
        since those are written to separate DB tables.
        """
        return self.model_dump(
            exclude={"experiences", "educations", "projects", "skills"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# JD schema
# ─────────────────────────────────────────────────────────────────────────────

class RequiredSkill(BaseModel):
    name:  str
    type:  str = "required"   # "required" | "preferred"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("skill name must not be blank")
        return v

    @field_validator("type")
    @classmethod
    def type_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("required", "preferred"):
            return "required"
        return v


class ParsedJD(BaseModel):
    """
    Parsed job description — used in context_gatherer_node and all downstream nodes.
    All scoring functions reference this schema instead of raw dict keys.
    """
    requisition_id:      int
    job_title:           str
    department:          Optional[str] = None
    seniority_level:     Optional[str] = None
    required_years:      int = Field(default=0, ge=0)
    max_years:           Optional[int] = None
    min_education_level: Optional[str] = None
    field_of_study:      Optional[str] = None
    key_responsibilities: str = ""
    full_description:    str = ""
    required_skills:     list[RequiredSkill] = Field(default_factory=list)

    @field_validator("required_years", mode="before")
    @classmethod
    def coerce_required_years(cls, v: Any) -> int:
        if v is None:
            return 0
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return 0

    @property
    def required_skill_names(self) -> list[str]:
        """All required (non-preferred) skill names."""
        return [s.name for s in self.required_skills if s.type == "required"]

    @property
    def preferred_skill_names(self) -> list[str]:
        return [s.name for s in self.required_skills if s.type == "preferred"]

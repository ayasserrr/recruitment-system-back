from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.cv_education import CVEducation
from models.db.cv_experience import CVExperience
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.schemas.cv_schema import ParsedCVSchema


class CVPersistenceService:
    """Persists a validated ParsedCVSchema into the relational database."""

    def _parse_date(self, value: object) -> Optional[date]:
        if not value:
            return None
        s = str(value).strip()
        if s.lower() in ("present", "current", "now", "ongoing", ""):
            return None
        for fmt in ("%Y-%m-%d", "%m/%Y", "%d/%m/%Y", "%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def persist(
        self,
        cv_parsed: ParsedCVSchema,
        db: Session,
        file_url: str = "",
        registered_candidate_id: Optional[int] = None,
    ) -> tuple[int, int]:
        """Map a validated ParsedCVSchema to DB rows within a single transaction.

        Returns (candidate_id, cv_id). Rolls back on any failure.
        """
        try:
            candidate_id, cv_id = self._persist_internal(
                cv_parsed, db, file_url, registered_candidate_id
            )
            db.commit()
            return candidate_id, cv_id
        except Exception:
            db.rollback()
            raise

    def _persist_internal(
        self,
        cv_parsed: ParsedCVSchema,
        db: Session,
        file_url: str,
        registered_candidate_id: Optional[int],
    ) -> tuple[int, int]:

        # ── 1. Candidate ────────────────────────────────────────────────
        candidate: Optional[Candidate] = None

        if registered_candidate_id is not None:
            candidate = (
                db.query(Candidate)
                .filter(Candidate.candidate_id == registered_candidate_id)
                .first()
            )

        if candidate is None:
            candidate = (
                db.query(Candidate).filter(Candidate.email == cv_parsed.email).first()
            )

        if candidate is None:
            candidate = Candidate(
                first_name=cv_parsed.first_name,
                last_name=cv_parsed.last_name,
                email=cv_parsed.email,
            )
            db.add(candidate)
        else:
            candidate.first_name = cv_parsed.first_name
            candidate.last_name = cv_parsed.last_name
            if candidate.email.endswith("@cv.local"):
                candidate.email = cv_parsed.email

        if cv_parsed.phone:
            candidate.phone = cv_parsed.phone
        if cv_parsed.linkedin_url:
            candidate.linkedin_url = cv_parsed.linkedin_url
        if cv_parsed.portfolio_url:
            candidate.portfolio_url = cv_parsed.portfolio_url
        if cv_parsed.professional_summary:
            candidate.professional_summary = cv_parsed.professional_summary
        if cv_parsed.years_of_experience is not None:
            candidate.years_of_experience = cv_parsed.years_of_experience
        if cv_parsed.education_level:
            candidate.education_level = cv_parsed.education_level
        if cv_parsed.field_of_study:
            candidate.field_of_study = cv_parsed.field_of_study

        db.flush()

        # ── 2. CandidateCV ──────────────────────────────────────────────
        file_name = file_url.split("/")[-1].split("\\")[-1] if file_url else ""
        cv_record = CandidateCV(
            candidate_id=candidate.candidate_id,
            file_url=file_url,
            file_name=file_name,
            is_primary=True,
        )
        db.add(cv_record)
        db.flush()
        cv_id: int = cv_record.cv_id

        # ── 3. CVExperience ─────────────────────────────────────────────
        for exp in cv_parsed.experiences:
            description: Optional[str] = None
            if exp.responsibilities:
                description = "\n".join(str(r) for r in exp.responsibilities if r) or None
            db.add(
                CVExperience(
                    cv_id=cv_id,
                    company_name=exp.company,
                    job_title=exp.job_title,
                    start_date=self._parse_date(exp.start_date),
                    end_date=self._parse_date(exp.end_date),
                    description=description,
                )
            )

        # ── 4. CVProject ────────────────────────────────────────────────
        for proj in cv_parsed.projects:
            tech_stack: Optional[str] = None
            if proj.tech_stack:
                tech_stack = ", ".join(str(t) for t in proj.tech_stack)
            db.add(
                CVProject(
                    cv_id=cv_id,
                    project_name=proj.project_name,
                    description=proj.description,
                    tech_stack=tech_stack,
                )
            )

        # ── 5. CVSkill ──────────────────────────────────────────────────
        for skill in cv_parsed.skills:
            db.add(
                CVSkill(
                    cv_id=cv_id,
                    skill_name=skill.skill_name,
                    proficiency_level=skill.proficiency_level,
                )
            )

        # ── 6. CVEducation ──────────────────────────────────────────────
        for edu in cv_parsed.educations:
            db.add(
                CVEducation(
                    cv_id=cv_id,
                    institution=edu.institution,
                    degree=edu.degree,
                    field=edu.field,
                    graduation_date=self._parse_date(edu.graduation_date),
                )
            )

        return candidate.candidate_id, cv_id

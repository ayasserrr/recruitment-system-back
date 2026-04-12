from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.cv_education import CVEducation
from models.db.cv_experience import CVExperience
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill


class CVPersistenceService:
    """Persists AI-parsed CV JSON into the relational database models."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_date(self, value: Any) -> Optional[date]:
        """Convert a date string from the CV JSON into a Python date.

        Supported formats: "MM/YYYY", "YYYY-MM-DD", "YYYY".
        "Present", "Current", empty string → None.
        """
        if not value:
            return None
        s = str(value).strip()
        if s.lower() in ("present", "current", "now", "ongoing", ""):
            return None
        for fmt in ("%m/%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_years(self, value: Any) -> Optional[int]:
        """Extract an integer year count from strings like '5', '5+', '5 years'."""
        if value is None:
            return None
        match = re.search(r"\d+", str(value))
        return int(match.group()) if match else None

    def _split_name(self, full_name: str) -> tuple[str, str]:
        parts = full_name.strip().split(" ", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def persist(
        self,
        cv_json: dict[str, Any],
        db: Session,
        file_url: str = "",
        registered_candidate_id: Optional[int] = None,
    ) -> tuple[int, int]:
        """Map parsed CV JSON to DB rows within a single transaction.

        If registered_candidate_id is provided, that existing Candidate row is
        updated in-place (no duplicate created).  Falls back to email lookup
        otherwise.

        Returns (candidate_id, cv_id).
        Rolls back the entire transaction on any failure.
        """
        try:
            candidate_id, cv_id = self._persist_internal(
                cv_json, db, file_url, registered_candidate_id
            )
            db.commit()
            return candidate_id, cv_id
        except Exception:
            db.rollback()
            raise

    # ------------------------------------------------------------------
    # Internal mapping logic
    # ------------------------------------------------------------------

    def _persist_internal(
        self,
        cv_json: dict[str, Any],
        db: Session,
        file_url: str,
        registered_candidate_id: Optional[int],
    ) -> tuple[int, int]:

        # ── 1. Candidate ────────────────────────────────────────────────
        candidate: Optional[Candidate] = None

        # Priority 1: look up the pre-registered row by DB ID
        if registered_candidate_id is not None:
            candidate = db.query(Candidate).filter(
                Candidate.candidate_id == registered_candidate_id
            ).first()

        # Priority 2: fall back to email match
        if candidate is None:
            email = (cv_json.get("email") or "").strip()
            if email:
                candidate = db.query(Candidate).filter(Candidate.email == email).first()

        full_name = (cv_json.get("name") or "").strip()
        first_name, last_name = self._split_name(full_name) if full_name else ("", "")

        if candidate is None:
            # No pre-registered candidate — create one now
            email = (cv_json.get("email") or "").strip()
            if not email:
                email = f"noemail_{int(datetime.utcnow().timestamp())}@cv.local"
            candidate = Candidate(
                first_name=first_name or "Unknown",
                last_name=last_name or "Unknown",
                email=email,
            )
            db.add(candidate)
        else:
            # Update the existing row with parsed data (overwrite placeholders)
            if first_name:
                candidate.first_name = first_name
            if last_name:
                candidate.last_name = last_name
            # Sync email if the AI found one and the current value is a placeholder
            parsed_email = (cv_json.get("email") or "").strip()
            if parsed_email and candidate.email.endswith("@cv.local"):
                candidate.email = parsed_email

        # Update enriched profile fields
        if cv_json.get("phone"):
            candidate.phone = cv_json["phone"]
        if cv_json.get("linkedin"):
            candidate.linkedin_url = cv_json["linkedin"]
        if cv_json.get("portfolio"):
            candidate.portfolio_url = cv_json["portfolio"]
        if cv_json.get("professional_summary"):
            candidate.professional_summary = cv_json["professional_summary"]

        years = self._parse_years(cv_json.get("experience_years"))
        if years is not None:
            candidate.years_of_experience = years

        education_obj: dict = cv_json.get("education") or {}
        if education_obj.get("degree"):
            candidate.education_level = education_obj["degree"]
        if education_obj.get("major"):
            candidate.field_of_study = education_obj["major"]

        db.flush()  # materialise candidate_id

        # ── 2. CandidateCV ──────────────────────────────────────────────
        file_name = file_url.split("/")[-1].split("\\")[-1] if file_url else ""
        cv_record = CandidateCV(
            candidate_id=candidate.candidate_id,
            file_url=file_url,
            file_name=file_name,
            is_primary=True,
        )
        db.add(cv_record)
        db.flush()  # materialise cv_id
        cv_id: int = cv_record.cv_id

        # ── 3. CVExperience ─────────────────────────────────────────────
        for exp in (cv_json.get("experience") or []):
            responsibilities = exp.get("responsibilities") or []
            if isinstance(responsibilities, list):
                description = "\n".join(str(r) for r in responsibilities if r)
            else:
                description = str(responsibilities)

            db.add(CVExperience(
                cv_id=cv_id,
                company_name=exp.get("company") or "",
                job_title=exp.get("job_title") or "",
                start_date=self._parse_date(exp.get("start_date")),
                end_date=self._parse_date(exp.get("end_date")),
                description=description,
            ))

        # ── 4. CVProject ────────────────────────────────────────────────
        for proj in (cv_json.get("key_projects") or []):
            technologies = proj.get("technologies") or []
            tech_stack = ", ".join(str(t) for t in technologies) if isinstance(technologies, list) else str(technologies)
            db.add(CVProject(
                cv_id=cv_id,
                project_name=proj.get("name") or "",
                description=proj.get("description") or "",
                tech_stack=tech_stack,
            ))

        # ── 5. CVSkill (technical skills only) ──────────────────────────
        skills_obj: dict = cv_json.get("skills") or {}
        for skill_name in (skills_obj.get("technical") or []):
            if skill_name:
                db.add(CVSkill(
                    cv_id=cv_id,
                    skill_name=str(skill_name),
                    proficiency_level=None,
                ))

        # ── 6. CVEducation ──────────────────────────────────────────────
        if education_obj:
            grad_year_raw = education_obj.get("graduation_year") or ""
            grad_date = self._parse_date(grad_year_raw)
            # Fallback: bare 4-digit year
            if grad_date is None and grad_year_raw:
                try:
                    grad_date = date(int(str(grad_year_raw).strip()), 6, 1)
                except (ValueError, TypeError):
                    grad_date = None

            db.add(CVEducation(
                cv_id=cv_id,
                institution=education_obj.get("university") or "",
                degree=education_obj.get("degree") or "",
                field=education_obj.get("major") or "",
                graduation_date=grad_date,
            ))

        return candidate.candidate_id, cv_id

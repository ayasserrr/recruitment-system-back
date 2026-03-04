from pathlib import Path

from .BaseController import BaseController


class CompanyController(BaseController):
    def __init__(self):
        super().__init__()

    def get_company_path(self, company_id: str, create: bool = True) -> Path:
        company_dir = self.uploads_dir / company_id
        if create:
            company_dir.mkdir(parents=True, exist_ok=True)
            return company_dir

        if company_dir.exists():
            return company_dir

        legacy = self.legacy_uploads_dir / company_id
        return legacy

    def get_job_path(self, company_id: str, job_id: str, create: bool = True) -> Path:
        job_dir = self.get_company_path(company_id=company_id, create=create) / job_id
        if create:
            job_dir.mkdir(parents=True, exist_ok=True)
            return job_dir

        if job_dir.exists():
            return job_dir

        legacy = (self.legacy_uploads_dir / company_id) / job_id
        return legacy

    def get_candidate_path(self, company_id: str, job_id: str, candidate_id: str, create: bool = True) -> Path:
        candidate_dir = self.get_job_path(company_id=company_id, job_id=job_id, create=create) / candidate_id
        if create:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            return candidate_dir

        if candidate_dir.exists():
            return candidate_dir

        legacy = ((self.legacy_uploads_dir / company_id) / job_id) / candidate_id
        return legacy

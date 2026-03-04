from __future__ import annotations

from pathlib import Path

import anyio
from langchain_community.document_loaders import PyMuPDFLoader, TextLoader

from .BaseController import BaseController
from .CompanyController import CompanyController


class ProcessController(BaseController):
    def __init__(self):
        super().__init__()
        self.company_controller = CompanyController()

    def _get_candidate_cv_path(self, company_id: str, job_id: str, candidate_id: str) -> Path:
        candidate_dir = self.company_controller.get_candidate_path(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
            create=False,
        )

        if not candidate_dir.exists():
            raise FileNotFoundError("No CV found for candidate")

        candidates = [
            p
            for p in candidate_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".pdf", ".txt"}
        ]

        if not candidates:
            raise FileNotFoundError("No CV found for candidate")

        candidates.sort(key=lambda p: p.name, reverse=True)
        return candidates[0]

    def _load_text_from_file(self, file_path: Path) -> str:
        if file_path.suffix.lower() == ".txt":
            loader = TextLoader(str(file_path), encoding="utf-8")
        elif file_path.suffix.lower() == ".pdf":
            loader = PyMuPDFLoader(str(file_path))
        else:
            raise ValueError("Unsupported file type")

        docs = loader.load()
        return "\n".join(d.page_content for d in docs if d.page_content)

    async def extract_candidate_text(self, company_id: str, job_id: str, candidate_id: str) -> str:
        cv_path = self._get_candidate_cv_path(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )
        return await anyio.to_thread.run_sync(self._load_text_from_file, cv_path)

    async def extract_job_text(self, company_id: str, job_id: str) -> str:
        job_dir = self.company_controller.get_job_path(company_id=company_id, job_id=job_id, create=False)

        if not job_dir.exists():
            return ""

        async def _candidate_text(candidate_dir: Path) -> str:
            candidate_id = candidate_dir.name
            return await self.extract_candidate_text(
                company_id=company_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )

        candidate_dirs = [p for p in job_dir.iterdir() if p.is_dir()]
        candidate_dirs.sort(key=lambda p: p.name)

        texts: list[str] = []
        for candidate_dir in candidate_dirs:
            try:
                texts.append(await _candidate_text(candidate_dir))
            except FileNotFoundError:
                continue

        return "\n".join(t for t in texts if t)

    async def extract_company_text(self, company_id: str) -> str:
        company_dir = self.company_controller.get_company_path(company_id=company_id, create=False)

        if not company_dir.exists():
            return ""

        job_dirs = [p for p in company_dir.iterdir() if p.is_dir()]
        job_dirs.sort(key=lambda p: p.name)

        texts: list[str] = []
        for job_dir in job_dirs:
            job_id = job_dir.name
            job_text = await self.extract_job_text(company_id=company_id, job_id=job_id)
            if job_text:
                texts.append(job_text)

        return "\n".join(texts)
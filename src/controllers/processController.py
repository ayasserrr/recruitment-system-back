from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import fitz  # PyMuPDF
from openai import AsyncOpenAI

from .BaseController import BaseController
from .CompanyController import CompanyController
from models.prompts import cv_system_prompt, cv_user_prompt
from models.schemas.cv_schema import ParsedCVSchema


class ProcessController(BaseController):
    def __init__(self):
        super().__init__()
        self.company_controller = CompanyController()

    async def _call_openai(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        client = AsyncOpenAI(api_key=self.app_settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        return json.loads(response.choices[0].message.content)

    async def extract_candidate_cv_json(
        self, company_id: str, job_id: str, candidate_id: str
    ) -> ParsedCVSchema:
        """Extract PDF text → call gpt-4o-mini → validate with Pydantic.

        Raises ValidationError if required fields (first_name, last_name, email,
        skill_name) are missing or empty.
        """
        cv_text = await self.extract_candidate_text(
            company_id=company_id, job_id=job_id, candidate_id=candidate_id
        )
        raw_json = await self._call_openai(
            system_prompt=cv_system_prompt(),
            user_prompt=cv_user_prompt(cv_text=cv_text),
        )
        return ParsedCVSchema.model_validate(raw_json)

    def _get_candidate_cv_path(
        self, company_id: str, job_id: str, candidate_id: str
    ) -> Path:
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
        if file_path.suffix.lower() == ".pdf":
            doc = fitz.open(str(file_path))
            pages = [page.get_text("text") for page in doc]
            doc.close()
            return "\n".join(p for p in pages if p)
        elif file_path.suffix.lower() == ".txt":
            return file_path.read_text(encoding="utf-8")
        else:
            raise ValueError("Unsupported file type")

    async def extract_candidate_text(
        self, company_id: str, job_id: str, candidate_id: str
    ) -> str:
        cv_path = self._get_candidate_cv_path(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )
        return await anyio.to_thread.run_sync(self._load_text_from_file, cv_path)

    async def extract_job_text(self, company_id: str, job_id: str) -> str:
        job_dir = self.company_controller.get_job_path(
            company_id=company_id, job_id=job_id, create=False
        )

        if not job_dir.exists():
            return ""

        async def _candidate_text(candidate_dir: Path) -> str:
            return await self.extract_candidate_text(
                company_id=company_id,
                job_id=job_id,
                candidate_id=candidate_dir.name,
            )

        candidate_dirs = sorted(
            [p for p in job_dir.iterdir() if p.is_dir()], key=lambda p: p.name
        )

        texts: list[str] = []
        for candidate_dir in candidate_dirs:
            try:
                texts.append(await _candidate_text(candidate_dir))
            except FileNotFoundError:
                continue

        return "\n".join(t for t in texts if t)

    async def extract_company_text(self, company_id: str) -> str:
        company_dir = self.company_controller.get_company_path(
            company_id=company_id, create=False
        )

        if not company_dir.exists():
            return ""

        job_dirs = sorted(
            [p for p in company_dir.iterdir() if p.is_dir()], key=lambda p: p.name
        )

        texts: list[str] = []
        for job_dir in job_dirs:
            job_text = await self.extract_job_text(
                company_id=company_id, job_id=job_dir.name
            )
            if job_text:
                texts.append(job_text)

        return "\n".join(texts)

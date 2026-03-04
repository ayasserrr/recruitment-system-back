from __future__ import annotations

import ast
from pathlib import Path
import json
from typing import Any

import anyio
import httpx
from langchain_community.document_loaders import PyMuPDFLoader, TextLoader

from .BaseController import BaseController
from .CompanyController import CompanyController
from models.prompts import cv_system_prompt, cv_user_prompt


class ProcessController(BaseController):
    def __init__(self):
        super().__init__()
        self.company_controller = CompanyController()

    def _extract_json(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found")
        candidate = text[start : end + 1]

        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = ast.literal_eval(candidate)

        if not isinstance(parsed, dict):
            raise ValueError("Extracted JSON is not an object")
        return parsed

    async def _ollama_chat(self, system_prompt: str, user_prompt: str) -> str:
        base_url = getattr(self.app_settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        model = getattr(self.app_settings, "OLLAMA_MODEL", "llama3.1")
        timeout = getattr(self.app_settings, "OLLAMA_TIMEOUT_SEC", 60)

        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, str):
                raise ValueError("Invalid Ollama response")
            return content

    async def extract_candidate_cv_json(self, company_id: str, job_id: str, candidate_id: str) -> dict[str, Any]:
        cv_text = await self.extract_candidate_text(company_id=company_id, job_id=job_id, candidate_id=candidate_id)
        system_prompt = cv_system_prompt()
        user_prompt = cv_user_prompt(cv_text=cv_text)

        last_error: str | None = None
        for _ in range(2):
            try:
                raw = await self._ollama_chat(system_prompt=system_prompt, user_prompt=user_prompt)
                return self._extract_json(raw)
            except Exception as e:
                last_error = str(e)
                user_prompt = user_prompt + "\n\nReturn STRICT JSON only. No markdown. No explanation."

        raise ValueError(f"cv_json_extraction_failed: {last_error}")

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
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from .BaseController import BaseController
from .CompanyController import CompanyController
from .processController import ProcessController


class RankController(BaseController):
    def __init__(self):
        super().__init__()
        self.company_controller = CompanyController()
        self.process_controller = ProcessController()

    async def _save_json(self, path: Path, payload: dict[str, Any]) -> None:
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False, indent=2))

    def _ranking_dir(self, company_id: str, job_id: str) -> Path:
        job_dir = self.company_controller.get_job_path(company_id=company_id, job_id=job_id, create=False)
        if not job_dir.exists():
            raise FileNotFoundError("Job folder not found")
        ranking_dir = job_dir / "ranking"
        ranking_dir.mkdir(parents=True, exist_ok=True)
        return ranking_dir

    def _candidate_dirs(self, company_id: str, job_id: str) -> list[Path]:
        job_dir = self.company_controller.get_job_path(company_id=company_id, job_id=job_id, create=False)
        if not job_dir.exists():
            raise FileNotFoundError("Job folder not found")
        candidates = [p for p in job_dir.iterdir() if p.is_dir() and p.name != "ranking"]
        candidates.sort(key=lambda p: p.name)
        return candidates

    def _system_prompt(self) -> str:
        return (
            "You are an expert recruitment assistant.\n"
            "Your job is to evaluate candidates based on\n"
            "their CV and the job requirements provided.\n"
            "Always respond in valid JSON only."
        )

    def _user_prompt(self, job_requirements: str, cv_text: str) -> str:
        return (
            "Job Requirements:\n"
            f"{job_requirements}\n\n"
            "Candidate CV:\n"
            f"{cv_text}\n\n"
            "Evaluate this candidate and return ONLY this JSON:\n"
            "{\n"
            "  'score': (0-100),\n"
            "  'strengths': ['...', '...'],\n"
            "  'weaknesses': ['...', '...'],\n"
            "  'summary': '...',\n"
            "  'recommendation': 'Highly Recommended' | 'Recommended' | 'Not Recommended'\n"
            "}"
        )

    def _extract_json(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found")
        candidate = text[start : end + 1]
        candidate = candidate.replace("'", '"')
        return json.loads(candidate)

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

    async def evaluate_candidate(self, company_id: str, job_id: str, candidate_id: str, job_requirements: str) -> dict[str, Any]:
        try:
            cv_text = await self.process_controller.extract_candidate_text(
                company_id=company_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )
        except Exception as e:
            return {
                "candidate_id": candidate_id,
                "score": 0,
                "strengths": [],
                "weaknesses": [],
                "summary": "",
                "recommendation": "Not Recommended",
                "error": f"cv_extraction_failed: {e}",
            }

        system_prompt = self._system_prompt()
        user_prompt = self._user_prompt(job_requirements=job_requirements, cv_text=cv_text)

        last_error: str | None = None
        for attempt in range(2):
            try:
                raw = await self._ollama_chat(system_prompt=system_prompt, user_prompt=user_prompt)
                parsed = self._extract_json(raw)
                return {
                    "candidate_id": candidate_id,
                    "score": int(parsed.get("score", 0)),
                    "strengths": parsed.get("strengths", []) or [],
                    "weaknesses": parsed.get("weaknesses", []) or [],
                    "summary": parsed.get("summary", "") or "",
                    "recommendation": parsed.get("recommendation", "Not Recommended") or "Not Recommended",
                }
            except Exception as e:
                last_error = str(e)
                user_prompt = (
                    user_prompt
                    + "\n\nReturn STRICT JSON only. No markdown. No explanation."
                )

        return {
            "candidate_id": candidate_id,
            "score": 0,
            "strengths": [],
            "weaknesses": [],
            "summary": "",
            "recommendation": "Not Recommended",
            "error": f"llm_failed: {last_error}",
        }

    async def rank_job(self, company_id: str, job_id: str, job_requirements: str) -> dict[str, Any]:
        candidate_dirs = self._candidate_dirs(company_id=company_id, job_id=job_id)
        results: list[dict[str, Any]] = []

        for candidate_dir in candidate_dirs:
            candidate_id = candidate_dir.name
            results.append(
                await self.evaluate_candidate(
                    company_id=company_id,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    job_requirements=job_requirements,
                )
            )

        def _score(item: dict[str, Any]) -> int:
            try:
                return int(item.get("score", 0))
            except Exception:
                return 0

        results.sort(key=_score, reverse=True)

        ranked: list[dict[str, Any]] = []
        rank = 1
        for rec in results:
            rec_out = {"rank": rank, **rec}
            ranked.append(rec_out)
            rank += 1

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate_reports: dict[str, str] = {}
        for rec in ranked:
            candidate_id = str(rec.get("candidate_id", ""))
            if not candidate_id:
                continue

            candidate_dir = self.company_controller.get_candidate_path(
                company_id=company_id,
                job_id=job_id,
                candidate_id=candidate_id,
                create=False,
            )
            if not candidate_dir.exists():
                continue

            candidate_report = {
                "company_id": company_id,
                "job_id": job_id,
                "job_requirements": job_requirements,
                "generated_at": ts,
                "candidate": rec,
            }

            candidate_report_path = candidate_dir / f"{candidate_id}_report.json"
            await self._save_json(candidate_report_path, candidate_report)
            candidate_reports[candidate_id] = str(candidate_report_path)

        report = {
            "company_id": company_id,
            "job_id": job_id,
            "job_requirements": job_requirements,
            "total_candidates": len(candidate_dirs),
            "ranked_candidates": ranked,
        }

        ranking_dir = self._ranking_dir(company_id=company_id, job_id=job_id)
        report_path = ranking_dir / f"{ts}.json"
        await self._save_json(report_path, report)

        report["report_path"] = str(report_path)
        report["candidate_report_paths"] = candidate_reports
        return report

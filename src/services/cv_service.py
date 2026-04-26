"""
CV Extraction Service — Canonical fitz-first pipeline
═══════════════════════════════════════════════════════

RULE: fitz (PyMuPDF) is the ONLY PDF reader. The LLM only *structures*
text that fitz already extracted. Never use an LLM to "read" a file.

Flow
────
  1. fitz_extract_text()  — local PyMuPDF extraction (fast, deterministic)
  2. parse_with_llm()     — GPT-4o-mini organizes the raw fitz text into the
                            canonical ParsedCV schema (same prompt used by
                            ProcessController.extract_candidate_cv_json)
  3. extract_cv_info()    — top-level entry point; returns (raw_text, ParsedCV)

The apply route (POST /api/v1/apply) stores raw_text immediately in
candidate_cvs.extracted_text, making re-parsing unnecessary for ranking.

Logging convention
──────────────────
  [FLOW-SYNC] CV {cv_id} parsed locally via fitz and persisted.
  [KNOWLEDGE-HIT] Skill '{skill}' retrieved from local DB.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from pydantic import ValidationError

from schemas.cv_schema import ParsedCV

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"
_MIN_CHAR_COUNT = 80  # reject files yielding fewer readable characters


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — raw text extraction (fitz is King)
# ─────────────────────────────────────────────────────────────────────────────

def fitz_extract_text(file_path: str, ext: str, cv_id: Optional[int] = None) -> str:
    """
    Extract raw text from a CV file using the local fitz (PyMuPDF) library.
    python-docx is used as a fallback for DOCX/DOC files.

    Returns the raw text string.
    Raises ValueError if the file is unreadable or yields fewer than
    MIN_CHAR_COUNT characters.

    Logging: logs the source and character count at INFO level.
    """
    ext_lower = ext.lower()

    if ext_lower == ".pdf":
        try:
            import fitz  # PyMuPDF — installed as pymupdf
            doc = fitz.open(file_path)
            page_count = doc.page_count
            if page_count > 5:
                logger.warning(
                    "[cv_service] cv_id=%s — PDF has %d pages; reading first 5 only.",
                    cv_id, page_count,
                )
            pages = [doc[i].get_text("text") for i in range(min(page_count, 5))]
            doc.close()
            raw = "\n".join(p for p in pages if p)

            # Fallback: pdfplumber if fitz yielded almost nothing
            if len(raw.strip()) < _MIN_CHAR_COUNT:
                logger.warning(
                    "[cv_service] cv_id=%s — fitz returned %d chars; trying pdfplumber.",
                    cv_id, len(raw.strip()),
                )
                try:
                    import pdfplumber  # type: ignore
                    with pdfplumber.open(file_path) as pdf:
                        raw = "\n".join(p.extract_text() or "" for p in pdf.pages[:5])
                    logger.info(
                        "[cv_service] cv_id=%s — pdfplumber recovered %d chars.",
                        cv_id, len(raw.strip()),
                    )
                except ImportError:
                    pass
                except Exception as plumb_exc:
                    logger.warning("[cv_service] pdfplumber fallback error: %s", plumb_exc)

            logger.info(
                "[cv_service] cv_id=%s — fitz extracted %d chars from %s.",
                cv_id, len(raw.strip()), file_path,
            )
            return raw

        except ImportError:
            raise ValueError(
                "PyMuPDF (fitz) is not installed. "
                "Install with: pip install pymupdf"
            )
        except Exception as exc:
            logger.error("[cv_service] fitz error for cv_id=%s: %s", cv_id, exc)
            raise ValueError(f"Could not read PDF: {exc}") from exc

    if ext_lower in (".doc", ".docx"):
        try:
            import docx  # python-docx
            doc = docx.Document(file_path)
            raw = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            logger.info(
                "[cv_service] cv_id=%s — python-docx extracted %d chars from %s.",
                cv_id, len(raw.strip()), file_path,
            )
            return raw
        except ImportError:
            raise ValueError(
                "python-docx is not installed. "
                "Install with: pip install python-docx"
            )
        except Exception as exc:
            logger.error("[cv_service] python-docx error for cv_id=%s: %s", cv_id, exc)
            raise ValueError(f"Could not read DOCX: {exc}") from exc

    raise ValueError(f"Unsupported file extension: {ext!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — LLM structuring (organizes fitz text; never reads the file)
# ─────────────────────────────────────────────────────────────────────────────

def parse_with_llm(
    raw_text: str,
    candidate_email_hint: str = "",
) -> ParsedCV:
    """
    Call GPT-4o-mini with the canonical ATS-parser prompt to convert fitz
    raw text into a validated ParsedCV object.

    Uses the same prompt schema as ProcessController.extract_candidate_cv_json()
    so the two code paths are interchangeable.

    Falls back to a heuristic-derived ParsedCV if the LLM is unavailable.
    The fallback uses cv_text_parser for skills and section extraction.

    Raises ValueError only when the result lacks first_name/last_name/email
    AND the fallback also fails.
    """
    from models.prompts.cv_prompts import cv_system_prompt, cv_user_prompt

    api_key = _get_api_key()

    raw_json: Optional[dict] = None

    if api_key:
        raw_json = _llm_call(raw_text, api_key, cv_system_prompt(), cv_user_prompt(cv_text=raw_text))

    if raw_json is not None:
        try:
            parsed = ParsedCV.model_validate(raw_json)
            logger.info(
                "[cv_service] LLM parsed CV: %s %s — %d skills, %d experiences, %d projects.",
                parsed.first_name, parsed.last_name,
                len(parsed.skills), len(parsed.experiences), len(parsed.projects),
            )
            return parsed
        except ValidationError as ve:
            logger.warning(
                "[cv_service] ParsedCV validation error after LLM — attempting fix: %s",
                ve,
            )
            # Try to patch mandatory fields from the email hint or raw text
            raw_json = _patch_mandatory_fields(raw_json, raw_text, candidate_email_hint)
            try:
                return ParsedCV.model_validate(raw_json)
            except ValidationError:
                pass  # fall through to heuristic

    logger.warning(
        "[cv_service] LLM unavailable or failed validation — using heuristic fallback."
    )
    return _heuristic_parse(raw_text, candidate_email_hint)


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def extract_cv_info(
    file_path: str,
    ext: str,
    cv_id: Optional[int] = None,
    candidate_email_hint: str = "",
) -> tuple[str, ParsedCV]:
    """
    Full pipeline: fitz extraction → LLM structuring → ParsedCV validation.

    Returns (raw_text, parsed_cv).

    Raises ValueError if:
      • The file yields fewer than MIN_CHAR_COUNT characters (empty/scanned).
      • The file extension is not supported.

    After a successful call, callers MUST persist raw_text into
    candidate_cvs.extracted_text and use CVPersistenceService.persist_cv_sub_tables()
    to write skills, experiences, educations, and projects to the DB.
    """
    raw_text = fitz_extract_text(file_path, ext, cv_id=cv_id)

    if len(raw_text.strip()) < _MIN_CHAR_COUNT:
        raise ValueError(
            "The uploaded CV appears to be empty or is a scanned image with no "
            "readable text. Please upload a text-based PDF, DOC, or DOCX file."
        )

    parsed_cv = parse_with_llm(raw_text, candidate_email_hint=candidate_email_hint)

    logger.info(
        "[FLOW-SYNC] CV %s parsed locally via fitz and persisted.",
        cv_id,
    )
    return raw_text, parsed_cv


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    try:
        from helpers.config import get_settings
        key = get_settings().OPENAI_API_KEY
        return key if key else None
    except Exception:
        return os.getenv("OPENAI_API_KEY")


def _llm_call(
    raw_text: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
) -> Optional[dict]:
    """Call OpenAI with the canonical prompts. Returns parsed dict or None."""
    try:
        resp = httpx.post(
            _OPENAI_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
                "max_tokens": 3000,
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        logger.error("[cv_service] LLM HTTP %d: %s", resp.status_code, resp.text[:300])
    except json.JSONDecodeError as jde:
        logger.error("[cv_service] JSON decode error from LLM: %s", jde)
    except Exception as exc:
        logger.error("[cv_service] LLM call error: %s", exc)
    return None


def _patch_mandatory_fields(
    raw_json: dict,
    raw_text: str,
    email_hint: str,
) -> dict:
    """
    Attempt to fill missing mandatory fields (first_name, last_name, email)
    from the raw CV text when the LLM omitted them.
    """
    patched = dict(raw_json)

    if not patched.get("email") and email_hint:
        patched["email"] = email_hint

    if not patched.get("first_name") or not patched.get("last_name"):
        # Try to extract a name from the first 10 lines
        from services.cv_text_parser import extract_name_from_text
        name_str = extract_name_from_text(raw_text)
        if name_str:
            parts = name_str.strip().split(" ", 1)
            if not patched.get("first_name"):
                patched["first_name"] = parts[0]
            if not patched.get("last_name") and len(parts) > 1:
                patched["last_name"] = parts[1]

    # Last-resort placeholders so Pydantic doesn't hard-fail on upload
    patched.setdefault("first_name", "Unknown")
    patched.setdefault("last_name", "Unknown")
    if not patched.get("email"):
        patched["email"] = email_hint or "unknown@cv.local"

    return patched


def _heuristic_parse(raw_text: str, email_hint: str = "") -> ParsedCV:
    """
    Build a minimal ParsedCV from heuristic extraction when the LLM is down.
    Uses cv_text_parser utilities (already fitz-based) for skills and sections.
    """
    from services.cv_text_parser import (
        extract_name_from_text,
        extract_section,
        extract_skills_from_text,
    )

    name_str = extract_name_from_text(raw_text) or ""
    parts = name_str.strip().split(" ", 1) if name_str else []
    first_name = parts[0] if parts else "Unknown"
    last_name = parts[1] if len(parts) > 1 else "Unknown"

    summary = extract_section(raw_text, ["summary", "objective", "profile", "about"])
    skills_raw = extract_skills_from_text(raw_text)

    edu_text = extract_section(raw_text, ["education", "academic background", "qualifications"])
    edu_lower = edu_text.lower()
    if "phd" in edu_lower or "doctorate" in edu_lower:
        edu_level = "PhD"
    elif any(k in edu_lower for k in ["master", "msc", "mba"]):
        edu_level = "Master"
    elif any(k in edu_lower for k in ["bachelor", "bsc", "b.sc"]):
        edu_level = "Bachelor"
    elif "diploma" in edu_lower:
        edu_level = "Diploma"
    else:
        edu_level = None

    return ParsedCV(
        first_name=first_name,
        last_name=last_name,
        email=email_hint or "unknown@cv.local",
        professional_summary=summary[:1000] if summary else None,
        education_level=edu_level,
        skills=[{"skill_name": s} for s in skills_raw],
    )

"""
CV Text Parser
──────────────
Phase 1 utilities for parsing structured CV data from raw text and PDF files.
Derived from the Gold Standard notebook Phase 1 pipeline.

Provides:
  • SECTION_HEADERS           — ordered list of recognized CV section names
  • extract_section()         — extract a named section from raw CV text
  • extract_name_from_text()  — smart 2–5 word name heuristic
  • extract_skills_from_text()— keyword-scan using canonical skill aliases
  • build_full_text()         — assemble normalized full_text for embedding
  • parse_cv_from_pdf()       — end-to-end PDF → structured dict (requires pymupdf)
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── import canonical alias map from ranking_graph (single source of truth) ────
try:
    from services.ranking_graph import _SKILL_ALIASES, normalize_skill  # type: ignore
except ImportError:
    # Fallback: empty alias map so the module still loads in isolation
    logger.warning("[cv_text_parser] Could not import _SKILL_ALIASES from ranking_graph; using empty map.")
    _SKILL_ALIASES: dict[str, str] = {}

    def normalize_skill(skill: str) -> str:  # type: ignore[override]
        key = skill.strip().lower()
        return _SKILL_ALIASES.get(key, key)


# ─────────────────────────────────────────────────────────────────────────────
# Section headers (Gold Standard SECTION_HEADERS)
# ─────────────────────────────────────────────────────────────────────────────

SECTION_HEADERS: list[str] = [
    "summary", "objective", "profile", "about",
    "experience", "work experience", "professional experience",
    "employment", "employment history", "work history",
    "education", "academic background", "qualifications",
    "skills", "technical skills", "core competencies", "competencies",
    "projects", "personal projects", "key projects",
    "certifications", "certificates", "licenses",
    "publications", "research",
    "awards", "honors", "achievements",
    "languages",
    "interests", "hobbies",
    "references",
    "volunteer", "volunteering",
    "courses", "training",
]

# Pre-compiled section boundary pattern (matches any known header on its own line)
_SECTION_RE = re.compile(
    r"^(" + "|".join(re.escape(h) for h in SECTION_HEADERS) + r")\s*[:\-]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Text normalization (Gold Standard normalize_text)
# ─────────────────────────────────────────────────────────────────────────────

_NOISE_RE = re.compile(r"[^\w\s\.\,\-\/\+\#\@]")


def normalize_text(text: str) -> str:
    """Lowercase, strip noise characters, collapse whitespace."""
    text = text.lower()
    text = _NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Section extractor
# ─────────────────────────────────────────────────────────────────────────────

def extract_section(
    text: str,
    section_names: list[str],
    next_sections: Optional[list[str]] = None,
) -> str:
    """
    Extract the content of a named CV section from raw text.

    Args:
        text:          Raw CV text.
        section_names: List of header variants to look for (e.g. ["experience", "work experience"]).
        next_sections: Headers that terminate the section. Defaults to all SECTION_HEADERS.

    Returns:
        Extracted section text, or "" if not found.
    """
    if next_sections is None:
        next_sections = SECTION_HEADERS

    lines = text.splitlines()
    in_section = False
    section_lines: list[str] = []

    stop_headers = {h.lower() for h in next_sections if h.lower() not in {s.lower() for s in section_names}}

    for line in lines:
        stripped = line.strip()
        lower    = stripped.lower().rstrip(":- ")

        if lower in {s.lower() for s in section_names}:
            in_section = True
            continue

        if in_section:
            if lower in stop_headers and stripped:
                break
            section_lines.append(line)

    return "\n".join(section_lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Name extractor
# ─────────────────────────────────────────────────────────────────────────────

_NAME_NOISE = re.compile(
    r"\b(resume|curriculum|vitae|cv|profile|summary|page|email|phone|address|linkedin|github)\b",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")


def extract_name_from_text(text: str) -> Optional[str]:
    """
    Heuristic name extraction: scan first 10 non-empty lines for a 2–5 word
    title-cased token that contains no digits and no known noise words.

    Returns the best candidate line, or None.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()][:10]
    for line in lines:
        if _NAME_NOISE.search(line):
            continue
        if _DIGIT_RE.search(line):
            continue
        words = line.split()
        if 2 <= len(words) <= 5 and all(w[0].isupper() for w in words if w):
            return line
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Skill extractor
# ─────────────────────────────────────────────────────────────────────────────

# Build a set of all known canonical skills (values of _SKILL_ALIASES) plus
# common standalone skills that appear in JDs but may not have aliases.
_KNOWN_SKILLS: set[str] = {
    "python", "javascript", "typescript", "java", "c++", "c#", "r", "scala", "go",
    "rust", "swift", "kotlin", "php", "ruby", "sql", "nosql",
    "tensorflow", "pytorch", "scikit-learn", "keras", "xgboost", "lightgbm",
    "machine learning", "deep learning", "nlp", "computer vision",
    "large language models", "transformers", "rag", "huggingface",
    "opencv", "yolo", "object detection", "image classification",
    "convolutional neural networks",
    "aws", "gcp", "azure", "docker", "kubernetes", "ci/cd",
    "git", "github", "linux", "bash",
    "fastapi", "flask", "django", "react", "angular", "vue",
    "nodejs", "rest api", "graphql",
    "postgresql", "mysql", "mongodb", "redis", "sqlite",
    "elasticsearch", "kafka", "spark", "hadoop",
    "pandas", "numpy", "matplotlib", "seaborn", "jupyter",
    "mlflow", "airflow", "dbt", "tableau", "power bi",
    "data science", "data analysis", "data engineering",
    "agile", "scrum", "jira",
    "faiss", "pinecone", "weaviate", "vector databases",
    "streamlit", "gradio",
    "object oriented programming", "design patterns", "microservices",
    "system design", "api", "websockets",
}
# Add all canonical alias targets
_KNOWN_SKILLS.update(_SKILL_ALIASES.values())


def extract_skills_from_text(text: str) -> list[str]:
    """
    Scan raw CV text for known skills using the canonical alias map.
    Returns a deduplicated list of canonical skill names found in the text.
    """
    lower_text = normalize_text(text)
    found: set[str] = set()

    # Check every known alias key
    for alias, canonical in _SKILL_ALIASES.items():
        # Word-boundary match to avoid partial hits (e.g. "r" in "research")
        try:
            if re.search(r"\b" + re.escape(alias) + r"\b", lower_text):
                found.add(canonical)
        except re.error:
            pass

    # Also check canonical skill names directly
    for skill in _KNOWN_SKILLS:
        try:
            if re.search(r"\b" + re.escape(skill) + r"\b", lower_text):
                found.add(skill)
        except re.error:
            pass

    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# Full-text builder
# ─────────────────────────────────────────────────────────────────────────────

def build_full_text(
    cv_data: dict,
    edu_text: str = "",
    exp_text: str = "",
    normalized_skills: Optional[list[str]] = None,
) -> str:
    """
    Assemble a normalized full_text string suitable for embedding or keyword search.
    Concatenates summary + education + experience + skills in a consistent order.

    Args:
        cv_data:           Parsed CV dict (from DB or LLM extraction).
        edu_text:          Pre-extracted education section text (optional).
        exp_text:          Pre-extracted experience section text (optional).
        normalized_skills: Canonical skill list (optional, falls back to cv_data["skills"]).

    Returns:
        Single normalized string.
    """
    parts: list[str] = []

    summary = cv_data.get("professional_summary") or ""
    if summary:
        parts.append(summary)

    if edu_text:
        parts.append(edu_text)
    else:
        for edu in cv_data.get("educations") or []:
            tokens = filter(None, [
                edu.get("degree"), edu.get("field"), edu.get("institution"),
            ])
            parts.append(" ".join(tokens))

    if exp_text:
        parts.append(exp_text)
    else:
        for exp in cv_data.get("experiences") or []:
            tokens = filter(None, [exp.get("job_title"), exp.get("company")])
            header = " ".join(tokens)
            resps  = " ".join(exp.get("responsibilities") or [])
            if header:
                parts.append(header)
            if resps:
                parts.append(resps)

    if normalized_skills:
        parts.append(" ".join(normalized_skills))
    else:
        raw_skills = cv_data.get("skills") or []
        skill_names = [
            (s.get("skill_name") or s) if isinstance(s, dict) else str(s)
            for s in raw_skills
        ]
        if skill_names:
            parts.append(" ".join(skill_names))

    for proj in cv_data.get("projects") or []:
        tokens = filter(None, [proj.get("project_name"), proj.get("description")])
        stack  = " ".join(proj.get("tech_stack") or [])
        parts.append(" ".join(tokens))
        if stack:
            parts.append(stack)

    return normalize_text(" ".join(parts))


# ─────────────────────────────────────────────────────────────────────────────
# PDF parser (requires pymupdf / fitz)
# ─────────────────────────────────────────────────────────────────────────────

def parse_cv_from_pdf(pdf_path: str, cv_id: Optional[int] = None) -> dict:
    """
    Extract structured CV data from a PDF file.

    Robustness guards applied in order:
      1. Page-count check — warn if > 5 pages (likely not a CV); parse first 5 only.
      2. Char-count check — if fitz extracts < 100 chars, fall back to pdfplumber.
      3. Language detection — warn if non-English (skill extraction may be unreliable).

    Returns a dict with:
        raw_text, name, skills, education_text, experience_text,
        projects_text, full_text, cv_id (passthrough),
        page_count, char_count, detected_language, warnings (list[str])

    Raises ImportError if neither pymupdf nor pdfplumber is installed.
    """
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "pymupdf (fitz) is required for PDF parsing. "
            "Install with: pip install pymupdf"
        ) from exc

    warnings: list[str] = []

    # ── Primary extraction: pymupdf ───────────────────────────────────────────
    doc        = fitz.open(pdf_path)
    page_count = doc.page_count

    # Guard 1: page count
    if page_count > 5:
        msg = (
            f"PDF has {page_count} pages — likely not a CV. "
            "Processing first 5 pages only."
        )
        logger.warning("[parse_cv_from_pdf] cv_id=%s — %s", cv_id, msg)
        warnings.append(msg)

    pages_text = [doc[i].get_text("text") for i in range(min(page_count, 5))]
    doc.close()

    raw_text   = "\n".join(pages_text)
    char_count = len(raw_text.strip())

    # Guard 2: near-empty extraction → fall back to pdfplumber
    if char_count < 100:
        msg = (
            f"fitz extracted only {char_count} characters — "
            "attempting pdfplumber fallback."
        )
        logger.warning("[parse_cv_from_pdf] cv_id=%s — %s", cv_id, msg)
        warnings.append(msg)
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(pdf_path) as pdf:
                plumber_pages = [
                    p.extract_text() or ""
                    for p in pdf.pages[:5]
                ]
            raw_text   = "\n".join(plumber_pages)
            char_count = len(raw_text.strip())
            warnings.append(
                f"pdfplumber recovered {char_count} characters."
            )
            logger.info(
                "[parse_cv_from_pdf] cv_id=%s — pdfplumber recovered %d chars.",
                cv_id, char_count,
            )
        except ImportError:
            msg = "pdfplumber not installed — cannot recover. Install: pip install pdfplumber"
            logger.error("[parse_cv_from_pdf] cv_id=%s — %s", cv_id, msg)
            warnings.append(msg)
        except Exception as plumber_exc:
            msg = f"pdfplumber fallback failed: {plumber_exc}"
            logger.error("[parse_cv_from_pdf] cv_id=%s — %s", cv_id, msg)
            warnings.append(msg)

    # Guard 3: language detection
    detected_language = "unknown"
    try:
        from langdetect import detect  # type: ignore
        detected_language = detect(raw_text[:500]) if raw_text.strip() else "unknown"
        if detected_language != "en":
            msg = (
                f"Detected language '{detected_language}' — skill extraction "
                "may be unreliable. Consider adding a translation step."
            )
            logger.warning("[parse_cv_from_pdf] cv_id=%s — %s", cv_id, msg)
            warnings.append(msg)
    except ImportError:
        pass  # langdetect optional — do not break pipeline
    except Exception:
        pass

    # ── Structured extraction ─────────────────────────────────────────────────
    name      = extract_name_from_text(raw_text)
    edu_text  = extract_section(
        raw_text,
        ["education", "academic background", "qualifications"],
    )
    exp_text  = extract_section(
        raw_text,
        ["experience", "work experience", "professional experience",
         "employment", "employment history"],
    )
    proj_text = extract_section(
        raw_text,
        ["projects", "personal projects", "key projects"],
    )
    skills    = extract_skills_from_text(raw_text)
    full_text = build_full_text(
        cv_data={},
        edu_text=edu_text,
        exp_text=exp_text,
        normalized_skills=skills,
    )

    logger.info(
        "[parse_cv_from_pdf] cv_id=%s  pages=%d  chars=%d  lang=%s  skills=%d  warnings=%d",
        cv_id, page_count, char_count, detected_language, len(skills), len(warnings),
    )

    return {
        "cv_id":             cv_id,
        "raw_text":          raw_text,
        "name":              name,
        "skills":            skills,
        "education_text":    edu_text,
        "experience_text":   exp_text,
        "projects_text":     proj_text,
        "full_text":         full_text,
        "page_count":        page_count,
        "char_count":        char_count,
        "detected_language": detected_language,
        "warnings":          warnings,
    }

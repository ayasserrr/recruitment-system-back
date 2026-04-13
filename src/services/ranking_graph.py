"""
AI Recruitment Intelligence v6.0 — LangGraph Ranking Workflow
═══════════════════════════════════════════════════════════════

Triggered via POST /api/v1/jobs/{jid}/rank-candidates (after CV collection deadline).

Graph nodes (sequential):
  1. context_gatherer_node      – load JD + all applications + full CV data from DB
                                  (eager-loads CVExperience, CVEducation, CVSkill, CVProject)
  2. deterministic_scoring_node – rule-based scoring with all 5 fixes applied
  3. llm_qualitative_node       – OpenAI GPT-4o-mini: project depth, strengths, concerns
  4. genai_validator_node        – GenAI evidence extraction + deployment context fix
  5. final_ranker_node           – tiered weights (Student vs Experience mode)
                                  + pool-relative labeling (Top Candidate / Strong Runner-Up …)
  6. persistence_node            – full upsert to semantic_analysis_reports +
                                  SemanticMatchedSkill; re-ranking is atomic (all rows updated)

Applied fixes (v6.0):
  [FIX-A]  AI/ML background detection for students with 0 formal experience
  [FIX-B]  Implicit teamwork credit: internship → team experience signal
  [FIX-C]  Education floor: strict degree-hierarchy enforcement
  [FIX-D]  Tiered keyword-stuffing penalty (>20 listed skills penalised)
  [FIX-E]  Deployment context validation: real deployment vs tutorial vs academic

Pool-relative labels (assigned after the entire pool is scored & sorted):
  Rank 1                        → "Top Candidate"
  Gap to top ≤ 10 pts           → "Strong Runner-Up"
  Score ≥ 70, top-33% of pool   → "Strong Hire"
  Score ≥ 55, top-60% of pool   → "Hire"
  Score ≥ 40                    → "Maybe"
  Score <  40                   → "No Hire"

Re-ranking atomicity:
  Each call re-scores the ENTIRE pool from scratch and upserts every
  SemanticAnalysisReport row.  Calling the endpoint again after a new
  application arrives will automatically re-number rank_in_pool for all
  existing candidates — no manual reset needed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import joinedload

from database.connection import SessionLocal
from helpers.config import get_settings
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.requisition_required_skill import RequisitionRequiredSkill
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.semantic_matched_skill import SemanticMatchedSkill

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"

# Degree hierarchy — index 0 is lowest
_EDUCATION_HIERARCHY = [
    "high school", "diploma", "associate", "bachelor", "undergraduate",
    "postgraduate", "master", "mba", "doctorate", "phd",
]

# Keywords that reveal AI/ML background in raw CV text [FIX-A]
_AI_KEYWORDS: set[str] = {
    "machine learning", "deep learning", "neural network", "nlp", "computer vision",
    "tensorflow", "pytorch", "keras", "scikit-learn", "sklearn", "pandas", "numpy",
    "transformers", "bert", "gpt", "llm", "rag", "langchain", "hugging face",
    "data science", "artificial intelligence", "reinforcement learning",
    "generative ai", "diffusion", "fine-tuning", "embeddings", "vector store",
    "opencv", "yolo", "stable diffusion", "openai", "groq api", "ollama",
}

# ── Rich skill synonym map (canonical → aliases) ─────────────────────────────
# Covers the full AI Engineer JD: core skills, secondary skills, and common
# spelling / abbreviation variants seen in real CVs.
_SKILL_SYNONYMS: dict[str, list[str]] = {
    "python": [
        "python", "fastapi", "django", "flask", "backend python",
        "scripting", "oop python", "pythonic", "python3",
    ],
    "ml_framework": [
        "tensorflow", "tf.", "tf2", "keras", "pytorch", "torch", "lightning",
        "deep learning", "neural network", "cnn", "convolutional neural",
        "deep neural", "model training", "model architecture", "jax",
        "tensorboard", "weights and biases", "wandb",
    ],
    "machine learning": [
        "machine learning", "ml model", "ml pipeline", "supervised learning",
        "unsupervised learning", "classification", "regression", "clustering",
        "random forest", "xgboost", "gradient boosting", "decision tree",
        "logistic regression", "predictive model", "feature engineering",
        "scikit", "sklearn", "ai model", "ai-powered", "ml-powered",
        "predictive analytics", "recommender system", "forecasting",
        "ensemble learning", "hyperparameter tuning", "svm", "knn",
    ],
    "deep learning": [
        "deep learning", "neural network", "cnn", "convolutional neural network",
        "rnn", "lstm", "transformer", "attention mechanism", "backpropagation",
        "batch normalization", "dropout", "fine-tuning", "transfer learning",
        "epoch", "loss function", "gan", "autoencoder", "resnet", "vgg",
        "pre-trained models", "neural architecture",
    ],
    "computer vision": [
        "computer vision", "image classification", "image segmentation",
        "object detection", "object recognition", "image processing", "cnn",
        "convolutional", "yolo", "opencv", "cv2", "visual recognition",
        "image analysis", "bounding box", "detection model", "mediapipe",
        "ocr", "facial recognition", "semantic segmentation", "easyocr", "paddleocr",
    ],
    "nlp": [
        "nlp", "natural language processing", "natural language", "language processing",
        "text classification", "named entity recognition", "ner", "text generation",
        "sentiment analysis", "language model", "nlp pipeline", "tokenization",
        "text processing", "information extraction", "transformers", "bert",
        "spacy", "nltk", "llm", "large language model", "langchain", "gpt",
        "llama", "gemini", "openai", "generative ai", "semantic", "chatbot",
        "conversational ai", "text summarization", "word2vec",
        "question answering", "information retrieval", "watsonx", "ibm watsonx",
    ],
    "deployment": [
        "fastapi", "fast api", "flask", "flask api", "rest api", "restful api",
        "api endpoint", "api development", "backend api", "web framework",
        "deployed", "deployment", "serving", "inference api", "model serving",
        "web app", "streamlit", "gradio", "docker", "containeriz", "kubernetes",
        "huggingface spaces", "hf spaces", "mlflow", "model registry",
        "web service", "hosted", "ci/cd", "mlops", "production system",
        "ngrok", "heroku", "render.com", "watsonx orchestrate",
    ],
    "data analysis": [
        "data analysis", "data analytics", "pandas", "numpy", "matplotlib",
        "seaborn", "plotly", "data visualization", "exploratory data analysis",
        "eda", "statistical analysis", "data processing", "data cleaning",
        "data preprocessing", "jupyter", "notebook", "data science", "scipy",
        "sql", "power bi", "tableau", "data wrangling",
    ],
    "scikit-learn": [
        "scikit-learn", "sklearn", "scikit learn", "scikit",
        "classification model", "regression model", "random forest", "svm",
        "support vector", "gradient boosting", "decision tree",
        "logistic regression", "k-means", "cross-validation", "grid search",
        "train_test_split", "standardscaler", "minmaxscaler",
    ],
    "nlp_pipeline": [
        "nlp pipeline", "text processing", "sequence labeling", "rag",
        "retrieval augmented", "vector database", "faiss", "pinecone",
        "embeddings", "chromadb", "similarity search", "knowledge base",
        "weaviate", "milvus", "semantic search",
    ],
    "prompt engineering": [
        "prompt engineering", "prompt design", "prompt tuning", "few-shot",
        "chain-of-thought", "system prompt", "zero-shot", "in-context learning",
        "prompt optimization", "cot", "llm prompt",
    ],
    "langchain": [
        "langchain", "lang chain", "langgraph", "lang graph", "llm agent",
        "ai agent", "agentic", "tool calling", "function calling",
        "llm orchestration", "chains", "document loaders",
    ],
    # Legacy keys kept for backward compatibility
    "tensorflow": ["tensorflow", "tf", "keras", "tf2"],
    "pytorch": ["pytorch", "torch"],
    "large language model": ["llm", "gpt", "language model", "claude", "gemini", "llama"],
    "retrieval augmented generation": ["rag", "retrieval augmented"],
    "mlops": ["ml ops", "machine learning operations", "model serving", "model deployment", "mlflow"],
    "vector database": ["vector db", "faiss", "chroma", "pinecone", "weaviate", "milvus", "qdrant"],
    "docker": ["containerization", "container", "dockerfile"],
    "aws": ["amazon web services", "ec2", "s3", "sagemaker", "lambda", "amazon cloud"],
    "gcp": ["google cloud", "google cloud platform", "bigquery", "vertex ai"],
    "azure": ["microsoft azure", "ms azure", "azure ml", "azure openai"],
    "sql": ["mysql", "postgresql", "postgres", "sqlite", "relational database", "t-sql"],
    "api": ["rest api", "restful", "graphql", "fastapi", "flask", "django"],
}

# Bonus / nice-to-have skills
_NICE_SYNONYMS: dict[str, list[str]] = {
    "opencv":      ["opencv", "cv2", "open cv", "image manipulation", "easyocr", "paddleocr"],
    "yolo":        ["yolo", "you only look once", "object detection", "yolov5", "yolov8", "ultralytics"],
    "langchain":   ["langchain", "lang chain", "langgraph", "lang graph", "llm agent", "ai agent", "watsonx"],
    "open-source": ["open-source", "open source", "github.com", "contributed to", "pull request", "github project"],
    "kaggle":      [
        "kaggle", "ai competition", "ml competition", "data science competition",
        "hackathon", "ecpc", "icpc", "programming competition", "competition winner",
        "rov competition", "sumo robot", "robotics competition", "codeforces", "leetcode",
    ],
}

# Soft skill keyword signals
_SOFT_SKILL_SYNONYMS: dict[str, list[str]] = {
    "problem_solving": [
        "problem solving", "problem-solving", "solved", "troubleshoot", "debug",
        "optimized", "improved", "resolved", "tackled", "creative solution",
        "overcame", "challenge", "innovative", "reducing", "reduced",
        "error rate", "accuracy",
    ],
    "teamwork": [
        "teamwork", "team work", "team", "collaborated", "collaboration",
        "worked with", "cross-functional", "group project", "team member",
        "together", "partnership", "coordinated", "joint", "co-developed",
        "team player", "cooperative",
        # Implicit teamwork signals (FIX-B)
        "intern", "internship", "joined", "on-site", "hybrid",
        "mentors", "mentor", "supervised", "cross-team", "department",
        "stakeholder", "client", "reporting to",
    ],
    "communication": [
        "communication", "interpersonal", "presented", "presentation",
        "documented", "documentation", "report", "communicated", "explained",
        "technical writing", "readme", "wrote", "articulated", "summarized",
        "dashboard", "delivered", "demo", "demonstrat", "briefed",
        "proposal", "trained", "workshop", "meeting",
    ],
    "analytical_thinking": [
        "analyzed", "analysis", "evaluated", "assessed", "investigated",
        "research", "studied", "measured", "compared", "data-driven",
        "insights", "findings", "metrics", "benchmark",
        "anomaly", "detection", "fraud", "pattern",
    ],
    "continuous_learning": [
        "course", "certificate", "certification", "learning", "studied",
        "self-taught", "bootcamp", "training", "workshop", "udemy",
        "coursera", "kaggle", "online", "upskill",
        "ibm", "nvidia", "huawei", "google", "iti", "deeplearning",
    ],
}

# Deployment action phrases — distinguish real deployment from cert mentions
_CLOUD_CERT_PHRASES = [
    "aws certified", "gcp certified", "google cloud certified",
    "azure certified", "cloud practitioner", "solutions architect",
    "cloud certification", "certified cloud", "cloud associate",
]
_DEPLOYMENT_ACTION_PHRASES = [
    "deployed", "hosted", "served", "built an api", "created an api",
    "built a rest api", "developed an api", "exposed via api",
    "model serving", "production deploy", "inference endpoint",
    "launched on", "published on", "running on", "containerized",
    "dockerized", "pushed to", "uploaded to", "integrated with api",
    "backend api", "api endpoint", "fastapi", "flask app", "streamlit app",
    "gradio app", "mlflow", "ngrok", "heroku", "render.com",
    "huggingface spaces", "hf spaces", "cloud deployment",
    "cloud hosted", "web service", "watsonx orchestrate",
]

# Certification issuer quality tiers
_ISSUER_TIERS: dict[str, float] = {
    "iti": 1.5, "information technology institute": 1.5,
    "nvidia": 1.5, "ibm": 1.5, "google": 1.5, "udacity": 1.5,
    "deeplearning.ai": 1.5, "deep learning ai": 1.5, "fast.ai": 1.5,
    "hugging face": 1.5, "huggingface": 1.5, "stanford": 1.5, "mit": 1.5,
    "microsoft": 1.3, "coursera": 1.0, "edx": 1.0,
    "aws": 1.0, "amazon": 1.0, "kaggle": 1.0, "datacamp": 1.0,
    "pluralsight": 1.0, "linkedin learning": 1.0,
    "pytorch": 1.0, "tensorflow": 1.0,
}

# GenAI evidence terms for validator node
_GENAI_TERMS = [
    "rag", "retrieval augmented", "langchain", "llm", "fine-tun", "lora", "qlora",
    "mlops", "model deployment", "inference server", "vector store", "embedding model",
    "hugging face", "openai api", "groq", "ollama", "diffusion model",
    "prompt engineering", "llama", "mistral", "gemini api",
]

# Deployment context signals [FIX-E]
_DEPLOY_SIGNALS: dict[str, list[str]] = {
    "production": [
        "deployed to", "in production", "serving", "api endpoint", "rest api",
        "docker", "kubernetes", "aws", "gcp", "azure", "cloud", "fastapi",
        "flask server", "uvicorn", "gunicorn", "live system", "real users",
    ],
    "tutorial": [
        "tutorial", "course project", "udemy", "coursera", "youtube", "hobby",
        "practice project", "learning exercise", "following along", "kaggle notebook",
    ],
    "academic": [
        "research paper", "thesis", "dissertation", "university project",
        "coursework", "submitted to", "academic", "class assignment",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

class RankingState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]
    jd_data: dict            # JD fields + required skills list
    candidates_data: list    # enriched records from DB
    scored_candidates: list  # + det_scores, matched_skills, applied_fixes
    llm_results: list        # + llm_scores (project_depth, strengths, …)
    validated_candidates: list  # + genai_data (evidence, context, bonus)
    ranked_candidates: list     # + final_score, rank_in_pool, recommendation
    error: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def llm_call(messages: list[dict], max_retries: int = 3, timeout: int = 60) -> Optional[str]:
    """
    POST to OpenAI chat completions (GPT-4o-mini) with exponential-backoff
    retry on 429 rate-limit errors.
    Returns the assistant message string, or None on total failure.
    """
    api_key = get_settings().OPENAI_API_KEY
    if not api_key:
        logger.warning("[llm_call] OPENAI_API_KEY not set — LLM calls skipped.")
        return None

    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _OPENAI_MODEL,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1200,
                },
                timeout=timeout,
            )

            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]

            if resp.status_code == 429:
                wait = min(30 * attempt, 90)
                logger.warning(
                    "[llm_call] Rate-limited (429) — waiting %ds (attempt %d/%d).",
                    wait, attempt, max_retries,
                )
                time.sleep(wait)
                continue

            logger.error(
                "[llm_call] HTTP %d on attempt %d/%d: %s",
                resp.status_code, attempt, max_retries, resp.text[:300],
            )

        except httpx.TimeoutException:
            logger.warning("[llm_call] Timeout on attempt %d/%d.", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(10 * attempt)
        except Exception as exc:
            logger.error("[llm_call] Error on attempt %d: %s", attempt, exc)
            break

    return None


def semantic_skill_check(
    required_skill: str,
    candidate_skills: list[str],
    raw_text: str,
) -> tuple[bool, str]:
    """
    Returns (matched, match_type) — 'exact' | 'semantic' | 'text' | ''.
    Rescues candidates who have the skill under a different name.
    """
    req_lower = required_skill.lower().strip()
    cand_lower = [s.lower() for s in candidate_skills]

    # 1. Exact name in skills list
    if any(req_lower in s for s in cand_lower):
        return True, "exact"

    text_lower = raw_text.lower()

    # 2. Synonym expansion through canonical groups
    for canonical, synonyms in _SKILL_SYNONYMS.items():
        if req_lower == canonical or req_lower in synonyms:
            for term in [canonical] + synonyms:
                if any(term in s for s in cand_lower):
                    return True, "semantic"
                if term in text_lower:
                    return True, "text"

    # 3. Direct substring in raw CV text
    if req_lower in text_lower:
        return True, "text"

    return False, ""


def _parse_exp_date(value) -> Optional[date]:
    """Coerce DB date / ISO string / None to a date object."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        val = value.strip().lower()
        if val in ("", "null", "none", "present"):
            return None
        for fmt in ("%Y-%m-%d", "%m/%Y", "%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _infer_exp_type(exp: dict) -> str:
    """Classify an experience entry as full-time / internship / freelance / training."""
    text = f"{exp.get('job_title', '')} {exp.get('description', '')}".lower()
    if any(k in text for k in ["intern", "internship", "trainee"]):
        return "internship"
    if any(k in text for k in ["freelance", "freelancer", "contract", "self-employed"]):
        return "freelance"
    if any(k in text for k in [
        "bootcamp", "course", "coursera", "udemy", "workshop",
        "training", "program", "summer training",
    ]):
        return "training"
    if any(k in text for k in [
        "engineer", "developer", "scientist", "analyst", "lead", "manager",
        "specialist", "architect", "researcher",
    ]):
        return "full-time"
    return "unknown"


def _calc_experience_breakdown(experiences: list[dict]) -> dict:
    """
    Separates work history into full-time / internship / freelance / training.

    Full-time intervals are merged (de-duplicated) before summing so that
    overlapping jobs are not double-counted.  Internship and freelance months
    are summed directly (no overlap check — typically non-overlapping).

    Returns a dict with:
      fulltime_years, internship_months, freelance_years, training_count
    """
    today = date.today()
    fulltime_intervals: list[tuple[date, date]] = []
    internship_months = 0
    freelance_months  = 0
    training_count    = 0

    for exp in (experiences or []):
        exp_type = (_infer_exp_type(exp))

        start = _parse_exp_date(exp.get("start_date"))
        if not start:
            if exp_type == "training":
                training_count += 1
            continue

        end_raw = exp.get("end_date")
        end = _parse_exp_date(end_raw) if end_raw else today
        if end is None:
            end = today

        if end <= start:
            end = today  # open-ended / bad data → treat as current

        months = max(0, (end.year - start.year) * 12 + (end.month - start.month))
        if months == 0:
            months = 1  # floor: at least 1 month

        if exp_type == "full-time":
            fulltime_intervals.append((start, end))
        elif exp_type == "internship":
            internship_months += months
        elif exp_type == "freelance":
            freelance_months += months
        elif exp_type == "training":
            training_count += 1

    # Merge overlapping full-time intervals before summing
    fulltime_years = 0.0
    if fulltime_intervals:
        fulltime_intervals.sort(key=lambda x: x[0])
        merged: list[list[date]] = [list(fulltime_intervals[0])]
        for s, e in fulltime_intervals[1:]:
            if s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        total_ft_months = sum(
            (e.year - s.year) * 12 + (e.month - s.month)
            for s, e in merged
        )
        fulltime_years = round(total_ft_months / 12, 1)

    # Cap internship at 36 months in student mode, 60 months otherwise
    internship_months = min(internship_months, 60)

    return {
        "fulltime_years":    fulltime_years,
        "internship_months": internship_months,
        "freelance_years":   round(freelance_months / 12, 1),
        "training_count":    training_count,
    }


def _education_floor_score(candidate_level: str, required_level: str) -> float:
    """[FIX-C] 0.0–1.0 score enforcing degree-hierarchy floor."""
    if not required_level:
        return 1.0

    cand_lower = (candidate_level or "").lower()
    req_lower = required_level.lower()

    cand_idx = max(
        (i for i, lvl in enumerate(_EDUCATION_HIERARCHY) if lvl in cand_lower),
        default=-1,
    )
    req_idx = max(
        (i for i, lvl in enumerate(_EDUCATION_HIERARCHY) if lvl in req_lower),
        default=-1,
    )

    if req_idx == -1:
        return 1.0      # unknown requirement → no penalty
    if cand_idx == -1:
        return 0.4      # unknown candidate level → partial credit
    if cand_idx >= req_idx:
        return 1.0
    return max(0.0, 1.0 - (req_idx - cand_idx) * 0.25)


# ── Deployment context validation ────────────────────────────────────────────

def _validate_deployment_context(cv_lower: str) -> bool:
    """
    Returns True only when the CV shows real deployment action, not just a
    cloud certification name.  Prevents cert-only CVs from claiming the
    deployment skill.
    """
    has_cert_only = any(p in cv_lower for p in _CLOUD_CERT_PHRASES)
    has_action    = any(p in cv_lower for p in _DEPLOYMENT_ACTION_PHRASES)
    return has_action if (has_cert_only and not has_action) else has_action


# ── Keyword coverage (technical_match) ───────────────────────────────────────

def _term_in_text(term: str, text: str) -> bool:
    term = term.lower()
    if " " in term or "-" in term or "." in term or "_" in term:
        return term in text
    return bool(re.search(r"\b" + re.escape(term) + r"\b", text))


def _skill_matched(canonical: str, cv_lower: str) -> bool:
    if canonical == "deployment":
        return _validate_deployment_context(cv_lower)
    aliases = _SKILL_SYNONYMS.get(canonical, [canonical])
    return any(_term_in_text(alias, cv_lower) for alias in aliases)


def _skill_in_projects(canonical: str, projects: list[dict]) -> bool:
    aliases = _SKILL_SYNONYMS.get(canonical, [canonical])
    for proj in projects:
        proj_text = " ".join([
            proj.get("project_name", ""),
            proj.get("description", ""),
            proj.get("tech_stack", ""),
        ]).lower()
        if any(_term_in_text(alias, proj_text) for alias in aliases):
            return True
    return False


def _calc_keyword_coverage(
    jd_required_skills: list[dict],
    cv_lower: str,
    projects: list[dict],
) -> tuple[float, list[dict]]:
    """
    Scores how well the candidate covers the JD's required skills using
    the rich synonym map + evidence-level weighting.

    Evidence levels:
      demonstrated   (used in a project AND deployed)  → 1.00
      used_in_project (in a project but not deployed)   → 0.85
      mentioned_only  (elsewhere in CV)                 → 0.50

    Returns (score 0-100, matched_skills list).
    """
    _EVIDENCE_WEIGHT = {
        "demonstrated":    1.00,
        "used_in_project": 0.85,
        "mentioned_only":  0.50,
    }

    required = [s for s in jd_required_skills if s.get("type") in ("required", None, "")]
    preferred = [s for s in jd_required_skills if s.get("type") == "preferred"]

    def _evidence(canonical: str) -> str:
        if not _skill_matched(canonical, cv_lower):
            return "not_found"
        if not _skill_in_projects(canonical, projects):
            return "mentioned_only"
        return "demonstrated" if _validate_deployment_context(cv_lower) else "used_in_project"

    matched_skills: list[dict] = []
    req_weighted_sum = 0.0

    for sk in required:
        canonical = sk["name"].lower()
        ev = _evidence(canonical)
        if ev != "not_found":
            w = _EVIDENCE_WEIGHT[ev]
            req_weighted_sum += w
            matched_skills.append({"skill": sk["name"], "match_type": ev})

    req_score = (req_weighted_sum / max(1, len(required))) * 70.0

    pref_matched = 0
    for sk in preferred:
        ev = _evidence(sk["name"].lower())
        if ev != "not_found":
            pref_matched += 1
            matched_skills.append({"skill": sk["name"], "match_type": ev})

    pref_score = (pref_matched / max(1, len(preferred))) * 30.0 if preferred else 0.0

    return round(min(100.0, req_score + pref_score), 1), matched_skills


# ── Tiered CV-stuffing detection ─────────────────────────────────────────────

def _detect_cv_stuffing_tiered(skill_names: list[str], cv_lower: str,
                                experiences: list[dict], projects: list[dict]) -> float:
    """
    Detects skills listed but unsupported by any work/project context.
    Returns a penalty (0.0–12.0 points) applied to technical_match.

    Thresholds (stuffing_ratio):
      > 0.60 → −12 pts
      > 0.50 → −6  pts
      > 0.40 → −3  pts  (only if ≥ 5 suspicious skills)
    """
    if not skill_names:
        return 0.0

    context_text = " ".join([
        " ".join(e.get("description", "") for e in experiences),
        " ".join(
            f"{p.get('project_name','')} {p.get('description','')} {p.get('tech_stack','')}"
            for p in projects
        ),
    ]).lower()

    def _covered(skill: str) -> bool:
        sl = skill.lower()
        if sl in context_text:
            return True
        for canonical, aliases in _SKILL_SYNONYMS.items():
            if sl == canonical or sl in aliases or any(
                len(a) > 4 and (a in sl or sl in a) for a in aliases
            ):
                if any(alias in context_text for alias in aliases):
                    return True
        return False

    suspicious = [s for s in skill_names if len(s) >= 3 and not _covered(s)]
    ratio = len(suspicious) / max(len(skill_names), 1)

    if ratio > 0.6 and len(suspicious) >= 5:
        return 12.0
    if ratio > 0.5 and len(suspicious) >= 5:
        return 6.0
    if ratio > 0.4 and len(suspicious) >= 5:
        return 3.0
    return 0.0


# ── Soft skills scoring ───────────────────────────────────────────────────────

def _score_soft_skills(cv_lower: str, internship_months: int) -> float:
    """
    Score five soft-skill dimensions using keyword evidence.
    Returns 0–100.

    [FIX-B/FIX-E] Any candidate with internship experience gets a floor
    boost to teamwork + communication so that real-world work is rewarded.
    """
    dim_scores: dict[str, float] = {}

    for dim, keywords in _SOFT_SKILL_SYNONYMS.items():
        hit = any(kw in cv_lower for kw in keywords)
        dim_scores[dim] = 1.0 if hit else 0.0

    # Implicit boost from internship experience
    if internship_months > 0:
        dim_scores["teamwork"]      = max(dim_scores["teamwork"],      0.7)
        dim_scores["communication"] = max(dim_scores["communication"], 0.6)

    avg = sum(dim_scores.values()) / max(len(dim_scores), 1)
    return round(min(100.0, avg * 100.0), 1)


# ── Nice-to-have scoring ──────────────────────────────────────────────────────

def _score_nice_to_have(cv_lower: str) -> float:
    """
    Score 0–100 for bonus/nice-to-have signals (opencv, yolo, langchain,
    open-source contributions, competition participation).
    """
    hits = sum(
        1 for aliases in _NICE_SYNONYMS.values()
        if any(_term_in_text(alias, cv_lower) for alias in aliases)
    )
    return round(min(100.0, hits * 20.0), 1)


# ── Training score from experience breakdown ──────────────────────────────────

def _calc_training_score_from_breakdown(exp_breakdown: dict) -> float:
    """
    Estimates training/certification quality from the experience breakdown.
    Score 0–100.

    Components:
      • internship months  → up to 45 pts (5 pts/month)
      • training entries   → up to 15 pts (5 pts each)
      • freelance years    → up to  5 pts
    """
    score = 0.0
    score += min(45.0, exp_breakdown["internship_months"] * 5.0)
    score += min(15.0, exp_breakdown["training_count"]    * 5.0)
    score += min(5.0,  exp_breakdown["freelance_years"]   * 5.0)
    return round(min(100.0, score), 1)


# ── Education fit (with AI-field raw-text fallback) ───────────────────────────

def _calc_education_fit(candidate: dict, jd: dict, cv_lower: str) -> float:
    """
    Education score 0–100.
    1. Degree hierarchy floor (0.0–1.0) from _education_floor_score.
    2. Field-of-study word-overlap bonus.
    3. [FIX-A] AI/ML field fallback: if the structured field is empty but
       CV text mentions AI-related education, apply a partial credit floor.
    """
    min_edu   = jd.get("min_education_level") or ""
    jd_field  = (jd.get("field_of_study") or "").lower()

    floor = _education_floor_score(candidate.get("education_level", ""), min_edu)

    cand_field = (candidate.get("field_of_study") or "").lower()

    # Fallback: scan raw text for education field keywords if DB field empty
    if not cand_field:
        ai_edu_keywords = [
            "artificial intelligence", "computer science", "data science",
            "machine learning", "software engineering", "information technology",
            "computer engineering", "statistics",
        ]
        for kw in ai_edu_keywords:
            if kw in cv_lower:
                cand_field = kw
                break

    if jd_field and cand_field:
        jd_words   = set(jd_field.split())
        cand_words = set(cand_field.split())
        overlap = len(jd_words & cand_words) / max(1, len(jd_words))
    else:
        overlap = 0.5  # neutral when not specified

    return round(min(100.0, floor * 70.0 + overlap * 30.0), 1)


def _build_raw_text(candidate: dict) -> str:
    """Concatenate all CV text fields into a single string for keyword scanning."""
    parts = [candidate.get("professional_summary") or ""]
    for exp in candidate.get("experiences", []):
        parts += [
            exp.get("company_name") or "",
            exp.get("job_title") or "",
            exp.get("description") or "",
        ]
    for edu in candidate.get("educations", []):
        parts += [
            edu.get("institution") or "",
            edu.get("degree") or "",
            edu.get("field") or "",
        ]
    for proj in candidate.get("projects", []):
        parts += [
            proj.get("project_name") or "",
            proj.get("description") or "",
            proj.get("tech_stack") or "",
        ]
    for sk in candidate.get("skills", []):
        parts.append(sk.get("name") or "")
    parts.append(candidate.get("cover_letter") or "")
    return " ".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — Context Gatherer
# ─────────────────────────────────────────────────────────────────────────────

def context_gatherer_node(state: RankingState) -> RankingState:
    """
    Fetches the JD (with required skills) and all candidate applications with
    their full CV data (experience, education, projects, skills) from the DB.
    """
    req_id = state["requisition_id"]
    logger.info("[context_gatherer] Loading data for requisition %d.", req_id)

    db = SessionLocal()
    try:
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == req_id)
            .first()
        )
        if not jr:
            return {**state, "error": f"JobRequisition {req_id} not found."}

        required_skills = (
            db.query(RequisitionRequiredSkill)
            .filter(RequisitionRequiredSkill.requisition_id == req_id)
            .all()
        )

        jd_data = {
            "requisition_id": req_id,
            "job_title": jr.job_title,
            "department": jr.department,
            "seniority_level": jr.seniority_level,
            "required_years": jr.min_years_experience or 0,
            "max_years": jr.max_years_experience,
            "min_education_level": jr.min_education_level,
            "field_of_study": jr.field_of_study,
            "key_responsibilities": jr.key_responsibilities or "",
            "full_description": jr.full_job_description or "",
            "required_skills": [
                {"name": s.skill_name, "type": s.skill_type or "required"}
                for s in required_skills
            ],
        }

        # JobPosting is needed to reach Applications
        posting: Optional[JobPosting] = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == req_id)
            .first()
        )

        if not posting:
            logger.warning(
                "[context_gatherer] No JobPosting for requisition %d — nothing to rank.", req_id
            )
            return {**state, "jd_data": jd_data, "posting_id": None, "candidates_data": []}

        posting_id = posting.posting_id

        # Eager-load the full candidate profile and all CV sub-tables in a
        # single round-trip.  This avoids N+1 lazy-load queries and prevents
        # DetachedInstanceError if the session is closed before data is read.
        applications = (
            db.query(Application)
            .filter(Application.posting_id == posting_id)
            .options(
                joinedload(Application.candidate),
                joinedload(Application.cv).joinedload(CandidateCV.skills),
                joinedload(Application.cv).joinedload(CandidateCV.experiences),
                joinedload(Application.cv).joinedload(CandidateCV.educations),
                joinedload(Application.cv).joinedload(CandidateCV.projects),
            )
            .all()
        )

        logger.info(
            "[context_gatherer] Found %d applications for posting %d.",
            len(applications), posting_id,
        )

        candidates_data: list[dict] = []
        for app in applications:
            try:
                candidate: Candidate = app.candidate
                cv: CandidateCV = app.cv

                record: dict = {
                    "application_id": app.application_id,
                    "candidate_id": candidate.candidate_id if candidate else None,
                    "candidate_name": (
                        f"{candidate.first_name} {candidate.last_name}"
                        if candidate else "Unknown"
                    ),
                    "email": candidate.email if candidate else "",
                    "professional_summary": (
                        candidate.professional_summary if candidate else ""
                    ),
                    "years_of_experience": (
                        candidate.years_of_experience or 0 if candidate else 0
                    ),
                    "education_level": (
                        candidate.education_level if candidate else ""
                    ),
                    "field_of_study": (
                        candidate.field_of_study if candidate else ""
                    ),
                    "cover_letter": app.cover_letter or "",
                    "skills": [
                        {"name": s.skill_name, "level": s.proficiency_level}
                        for s in (cv.skills if cv else [])
                    ],
                    "experiences": [
                        {
                            "company_name": e.company_name,
                            "job_title": e.job_title,
                            "start_date": e.start_date,
                            "end_date": e.end_date,
                            "description": e.description,
                        }
                        for e in (cv.experiences if cv else [])
                    ],
                    "educations": [
                        {
                            "institution": e.institution,
                            "degree": e.degree,
                            "field": e.field,
                            "graduation_date": str(e.graduation_date) if e.graduation_date else None,
                        }
                        for e in (cv.educations if cv else [])
                    ],
                    "projects": [
                        {
                            "project_name": p.project_name,
                            "description": p.description,
                            "tech_stack": p.tech_stack,
                        }
                        for p in (cv.projects if cv else [])
                    ],
                }
                record["raw_text"] = _build_raw_text(record)
                candidates_data.append(record)

            except Exception as exc:
                logger.warning(
                    "[context_gatherer] Skipping application %d — %s",
                    app.application_id, exc,
                )

        return {
            **state,
            "jd_data": jd_data,
            "posting_id": posting_id,
            "candidates_data": candidates_data,
        }

    except Exception as exc:
        logger.exception("[context_gatherer] Fatal error.")
        return {**state, "error": f"Context gather failed: {exc}"}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — Deterministic Scoring
# ─────────────────────────────────────────────────────────────────────────────

def deterministic_scoring_node(state: RankingState) -> RankingState:
    """
    Rule-based scoring for each candidate using the v6.0 algorithm.

    Scores produced (0–100 each):
      • technical_match   — rich synonym-map coverage + evidence levels + stuffing penalty
      • experience_quality— fulltime years OR internship months (student mode)
      • education_fit     — degree hierarchy floor + field overlap + AI-field fallback
      • soft_skills       — 5 dimensions via keyword evidence + internship implicit boost
      • training_score    — internship months + training entries + freelance credit
      • nice_to_have      — bonus signals (opencv, yolo, langchain, competitions, OSS)
    """
    jd = state["jd_data"]
    required_years: int = jd.get("required_years") or 0
    required_skills: list[dict] = jd.get("required_skills", [])

    scored: list[dict] = []

    for cand in state["candidates_data"]:
        try:
            raw_text: str = cand.get("raw_text", "")
            cv_lower = raw_text.lower()
            skill_names: list[str] = [s["name"] for s in cand.get("skills", [])]
            projects   = cand.get("projects", [])
            experiences = cand.get("experiences", [])
            fixes_applied: list[str] = []

            # ── Experience breakdown (fulltime / internship / freelance / training) ──
            exp_breakdown = _calc_experience_breakdown(experiences)
            fulltime_years    = exp_breakdown["fulltime_years"]
            internship_months = exp_breakdown["internship_months"]

            # ── [FIX-A] AI/ML background detection ───────────────────────────
            ai_background = any(kw in cv_lower for kw in _AI_KEYWORDS)
            if ai_background and fulltime_years == 0 and internship_months == 0:
                fixes_applied.append(
                    "[FIX-A] AI/ML background detected — experience floor lifted"
                )

            # ── Experience quality score ──────────────────────────────────────
            if required_years == 0:
                # Student mode: internship + AI background are primary signals
                exp_score = min(100.0, internship_months * 5.0)
                if exp_score == 0 and ai_background:
                    exp_score = 30.0  # [FIX-A] baseline for AI students
            else:
                candidate_years = fulltime_years + exp_breakdown["freelance_years"] * 0.5
                if candidate_years >= required_years:
                    exp_score = 90.0
                elif candidate_years >= required_years - 1:
                    exp_score = 75.0
                elif candidate_years >= required_years - 2:
                    exp_score = 58.0
                elif candidate_years >= 1:
                    exp_score = 40.0
                elif candidate_years > 0:
                    exp_score = 22.0
                elif ai_background:
                    exp_score = 20.0  # [FIX-A]
                else:
                    exp_score = 8.0

            # ── Education fit [FIX-C + FIX-A field fallback] ─────────────────
            edu_score = _calc_education_fit(cand, jd, cv_lower)

            # ── Technical match — rich synonym map + evidence levels ──────────
            raw_tech_score, matched_skills = _calc_keyword_coverage(
                required_skills, cv_lower, projects
            )

            # Tiered stuffing penalty [FIX-D]
            stuffing_penalty = _detect_cv_stuffing_tiered(
                skill_names, cv_lower, experiences, projects
            )
            if stuffing_penalty > 0:
                fixes_applied.append(
                    f"[FIX-D] CV stuffing penalty −{stuffing_penalty:.0f}pt"
                )

            technical_match = max(0.0, raw_tech_score - stuffing_penalty)

            # ── Soft skills [FIX-B / FIX-E] ──────────────────────────────────
            soft_score = _score_soft_skills(cv_lower, internship_months)
            if internship_months > 0:
                fixes_applied.append(
                    f"[FIX-B/FIX-E] Soft skills implicit boost "
                    f"from {internship_months}mo internship"
                )

            # ── Training score ────────────────────────────────────────────────
            training_score = _calc_training_score_from_breakdown(exp_breakdown)

            # ── Nice-to-have bonus ────────────────────────────────────────────
            nice_score = _score_nice_to_have(cv_lower)

            total_experience_months = int(fulltime_years * 12) + internship_months

            scored.append({
                **cand,
                "total_experience_months": total_experience_months,
                "fulltime_years":          fulltime_years,
                "internship_months":       internship_months,
                "ai_background_detected":  ai_background,
                "matched_skills":          matched_skills,
                "stuffing_penalty":        stuffing_penalty,
                "applied_fixes":           fixes_applied,
                "det_scores": {
                    "technical_match":    round(technical_match, 1),
                    "experience_quality": round(exp_score, 1),
                    "education_fit":      round(edu_score, 1),
                    "soft_skills":        round(soft_score, 1),
                    "training_score":     round(training_score, 1),
                    "nice_to_have":       round(nice_score, 1),
                },
            })

        except Exception as exc:
            logger.warning(
                "[deterministic_scoring] Error on candidate %s — %s",
                cand.get("candidate_name"), exc,
            )
            scored.append({
                **cand,
                "total_experience_months": 0,
                "fulltime_years":          0.0,
                "internship_months":       0,
                "ai_background_detected":  False,
                "matched_skills":          [],
                "stuffing_penalty":        0.0,
                "applied_fixes":           ["[ERROR] Deterministic scoring failed — zeros applied"],
                "det_scores": {
                    "technical_match":    0.0,
                    "experience_quality": 0.0,
                    "education_fit":      0.0,
                    "soft_skills":        0.0,
                    "training_score":     0.0,
                    "nice_to_have":       0.0,
                },
            })

    logger.info("[deterministic_scoring] Scored %d candidates.", len(scored))
    return {**state, "scored_candidates": scored}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — LLM Qualitative Assessment
# ─────────────────────────────────────────────────────────────────────────────

def _build_llm_prompt(cand: dict, jd: dict) -> list[dict]:
    """Construct the Groq chat messages for a single candidate assessment."""
    total_yrs = cand["total_experience_months"] // 12
    total_mo = cand["total_experience_months"] % 12
    intern_mo = cand["internship_months"]

    edu_parts = [
        f'{e.get("degree", "")} in {e.get("field", "")} at {e.get("institution", "")}'.strip()
        for e in cand.get("educations", [])
    ]
    edu_text = "; ".join(edu_parts) or "Not specified"

    proj_parts = []
    for p in cand.get("projects", []):
        name = p.get("project_name") or "Unnamed"
        desc = (p.get("description") or "")[:300]
        stack = p.get("tech_stack") or ""
        proj_parts.append(f"• {name}: {desc} [Stack: {stack}]")
    proj_text = "\n".join(proj_parts) or "No projects listed."

    exp_parts = []
    for e in cand.get("experiences", []):
        title = e.get("job_title") or "?"
        company = e.get("company_name") or "?"
        desc = (e.get("description") or "")[:200]
        exp_parts.append(f"• {title} @ {company}: {desc}")
    exp_text = "\n".join(exp_parts) or "No formal work experience."

    skills_text = ", ".join(s["name"] for s in cand.get("skills", []))[:400] or "None listed"

    system_msg = (
        "You are a senior AI/ML technical recruiter. "
        "Evaluate candidates objectively for technical roles. "
        "ALWAYS respond with valid JSON only — no markdown, no prose."
    )

    user_msg = f"""## Job Position
Title: {jd.get("job_title")}
Required Experience: {jd.get("required_years")} years | Seniority: {jd.get("seniority_level", "Not specified")}
Responsibilities: {(jd.get("key_responsibilities") or "")[:400]}

## Candidate: {cand.get("candidate_name")}
Total Experience: {total_yrs}y {total_mo}m (internship: {intern_mo}mo)
Education: {edu_text}
Skills: {skills_text}

## Projects
{proj_text}

## Work Experience
{exp_text}

Respond with VALID JSON (no markdown fences):
{{
  "project_depth_score": <integer 0-100>,
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "concerns": ["<concern 1>", "<concern 2>"],
  "interview_questions": ["<question 1>", "<question 2>", "<question 3>"],
  "summary": "<2–3 sentence overall assessment>"
}}"""

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _parse_llm_response(raw: str) -> tuple[dict, bool]:
    """
    Parse LLM JSON response.  Returns (parsed_dict, success).
    Strips markdown fences if present before parsing.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
        return data, True
    except json.JSONDecodeError:
        # Attempt to extract JSON object from embedded prose
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end])
                return data, True
            except json.JSONDecodeError:
                pass
    return {}, False


def llm_qualitative_node(state: RankingState) -> RankingState:
    """
    Calls OpenAI GPT-4o-mini for each candidate to assess project depth,
    strengths, concerns, and generate interview questions.
    Falls back to deterministic score + 'Manual Review Recommended' on failure.
    """
    jd = state["jd_data"]
    results: list[dict] = []

    for cand in state["scored_candidates"]:
        candidate_name = cand.get("candidate_name", "Unknown")
        try:
            messages = _build_llm_prompt(cand, jd)
            raw_response = llm_call(messages)

            if raw_response is None:
                raise ValueError("OpenAI returned None — API unavailable or key missing.")

            parsed, ok = _parse_llm_response(raw_response)
            if not ok or "project_depth_score" not in parsed:
                raise ValueError(f"Invalid LLM JSON: {raw_response[:200]}")

            llm_scores = {
                "project_depth": min(100, max(0, int(parsed.get("project_depth_score", 50)))),
                "strengths": parsed.get("strengths", [])[:5],
                "concerns": parsed.get("concerns", [])[:4],
                "interview_questions": parsed.get("interview_questions", [])[:5],
                "llm_summary": parsed.get("summary", ""),
            }
            logger.info(
                "[llm_qualitative] %s — project_depth=%d",
                candidate_name, llm_scores["project_depth"],
            )

        except Exception as exc:
            logger.warning(
                "[llm_qualitative] LLM failed for %s (%s) — falling back to deterministic.",
                candidate_name, exc,
            )
            # Graceful fallback: estimate project depth from project count
            proj_count = len(cand.get("projects", []))
            estimated_depth = min(100, proj_count * 18)

            llm_scores = {
                "project_depth": estimated_depth,
                "strengths": ["Manual review recommended"],
                "concerns": ["LLM assessment unavailable"],
                "interview_questions": [
                    "Please describe your most technically challenging project.",
                    "Walk us through your experience with the required tech stack.",
                    "How do you approach debugging complex systems?",
                ],
                "llm_summary": "Manual Review Recommended — automated assessment unavailable.",
            }
            cand = {
                **cand,
                "applied_fixes": cand.get("applied_fixes", []) + ["[FALLBACK] LLM unavailable — deterministic only"],
            }

        results.append({**cand, "llm_scores": llm_scores, "llm_failed": llm_scores["strengths"] == ["Manual review recommended"]})

    logger.info("[llm_qualitative] Completed LLM assessment for %d candidates.", len(results))
    return {**state, "llm_results": results}


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — GenAI Validator
# ─────────────────────────────────────────────────────────────────────────────

def genai_validator_node(state: RankingState) -> RankingState:
    """
    Extracts GenAI evidence (RAG, LLMs, MLOps, fine-tuning…) from raw CV text
    and applies [FIX-E] Deployment Context Validation:
      - "production" context → full bonus (up to +15 pts)
      - "academic"   context → partial bonus (+7 pts)
      - "tutorial"   context → minimal bonus (+2 pts)
      - no evidence           → 0 pts
    """
    validated: list[dict] = []

    for cand in state["llm_results"]:
        try:
            text_lower = cand.get("raw_text", "").lower()

            # Find GenAI terms present in text
            evidence: list[str] = [
                term for term in _GENAI_TERMS if term in text_lower
            ]

            # Determine deployment context
            context = "none"
            for ctx_name, signals in _DEPLOY_SIGNALS.items():
                if any(sig in text_lower for sig in signals):
                    context = ctx_name
                    break  # first match wins (production > tutorial > academic)

            # Bonus assignment
            if not evidence:
                bonus = 0.0
            elif context == "production":
                bonus = 15.0
            elif context == "academic":
                bonus = 7.0
            elif context == "tutorial":
                bonus = 2.0
            else:
                bonus = 5.0  # evidence present but context unclear → partial

            fixes = cand.get("applied_fixes", [])
            if evidence:
                fixes = fixes + [
                    f"[FIX-E] GenAI evidence found ({len(evidence)} terms) — "
                    f"context: {context} → +{bonus:.0f}pt bonus"
                ]

            validated.append({
                **cand,
                "applied_fixes": fixes,
                "genai_data": {
                    "evidence": evidence[:10],
                    "context": context,
                    "bonus": bonus,
                },
            })

        except Exception as exc:
            logger.warning(
                "[genai_validator] Error on %s — %s",
                cand.get("candidate_name"), exc,
            )
            validated.append({
                **cand,
                "genai_data": {"evidence": [], "context": "none", "bonus": 0.0},
            })

    logger.info("[genai_validator] Validated %d candidates.", len(validated))
    return {**state, "validated_candidates": validated}


# ─────────────────────────────────────────────────────────────────────────────
# Node 5 — Final Ranker
# ─────────────────────────────────────────────────────────────────────────────

# ── Dynamic weight sets (v6.0 rebalanced) ────────────────────────────────────
# Student mode  (required_years == 0): projects + skills are primary signals.
#   training_score lowered, education raised, soft/nice are tie-breakers.
# Experience mode (required_years > 0): years + skills dominate.
_WEIGHTS_STUDENT = {
    "technical_match":    0.35,
    "project_depth":      0.35,   # from LLM node
    "education_fit":      0.20,
    "training_score":     0.05,
    "soft_skills":        0.02,
    "nice_to_have":       0.03,
}

_WEIGHTS_EXPERIENCE = {
    "experience_quality": 0.35,
    "technical_match":    0.30,
    "project_depth":      0.25,   # from LLM node
    "education_fit":      0.05,
    "soft_skills":        0.02,
    "nice_to_have":       0.03,
}


def _pool_relative_label(
    rank: int,
    score: float,
    top_score: float,
    pool_size: int,
) -> str:
    """
    Assign a pool-relative recommendation label that compares each candidate
    to the top scorer rather than using only absolute thresholds.

    Logic:
      • Rank 1 (highest score)         → "Top Candidate"
      • Gap to top ≤ 10 pts            → "Strong Runner-Up"
        (e.g. top=90, this=85 → labeled Strong Runner-Up, not just Hire)
      • Score ≥ 70  AND top-33% pool   → "Strong Hire"
      • Score ≥ 55  AND top-60% pool   → "Hire"
      • Score ≥ 40                     → "Maybe"
      • Score <  40                    → "No Hire"

    The percentile_rank (0.0=last, 1.0=first) is used as a pool-size-aware
    tiebreaker so a "70" in a pool of 3 is treated differently from a "70"
    in a pool of 50.
    """
    if pool_size == 1:
        return "Top Candidate"
    if rank == 1:
        return "Top Candidate"

    gap = top_score - score
    if gap <= 10.0:
        return "Strong Runner-Up"

    # percentile_rank: 1.0 = best non-top, 0.0 = last
    percentile_rank = 1.0 - (rank - 1) / max(1, pool_size - 1)

    if score >= 70 and percentile_rank >= 0.6:
        return "Strong Hire"
    if score >= 55 and percentile_rank >= 0.4:
        return "Hire"
    if score >= 40:
        return "Maybe"
    return "No Hire"


def _absolute_tier(score: float) -> str:
    """Secondary absolute-threshold label stored alongside the pool-relative one."""
    if score >= 85:
        return "Strong Hire"
    if score >= 70:
        return "Hire"
    if score >= 55:
        return "Maybe"
    if score >= 40:
        return "Borderline"
    return "No Hire"


def final_ranker_node(state: RankingState) -> RankingState:
    """
    1. Computes each candidate's weighted final_score (0–100).
    2. Sorts the ENTIRE pool descending — pool-based comparison.
    3. Assigns rank_in_pool (1 = best) and a pool-relative recommendation label.

    Student mode  (required_years == 0): project_depth 35%, education 20%,
                                          skills 25%, experience 5%, teamwork 15%
    Experience mode (required_years > 0): experience 35%, skills 30%,
                                           education 15%, project_depth 10%, teamwork 10%
    """
    jd = state["jd_data"]
    required_years: int = jd.get("required_years") or 0

    mode = "Student Mode" if required_years == 0 else "Experience Mode"
    weights = _WEIGHTS_STUDENT if required_years == 0 else _WEIGHTS_EXPERIENCE

    # ── Phase 1: score every candidate independently ──────────────────────────
    scored: list[dict] = []

    for cand in state["validated_candidates"]:
        try:
            det = cand.get("det_scores", {})
            llm = cand.get("llm_scores", {})
            genai = cand.get("genai_data", {})

            component_scores = {
                "technical_match":    det.get("technical_match", 0.0),
                "experience_quality": det.get("experience_quality", 0.0),
                "education_fit":      det.get("education_fit", 0.0),
                "soft_skills":        det.get("soft_skills", 0.0),
                "training_score":     det.get("training_score", 0.0),
                "nice_to_have":       det.get("nice_to_have", 0.0),
                "project_depth":      float(llm.get("project_depth", 0)),
            }

            weighted_sum = sum(
                component_scores.get(k, 0.0) * v for k, v in weights.items()
            )

            genai_bonus = genai.get("bonus", 0.0)
            final_score = round(min(100.0, weighted_sum + genai_bonus), 2)

            scored.append({
                **cand,
                "final_score": final_score,
                "weighted_sum": round(weighted_sum, 2),
                "genai_bonus_applied": genai_bonus,
                "scoring_mode": mode,
                "weight_breakdown": weights,
                "component_scores": component_scores,
                # Temporary absolute tier — overwritten with pool-relative label below
                "recommendation": _absolute_tier(final_score),
            })

        except Exception as exc:
            logger.warning(
                "[final_ranker] Error computing score for %s — %s",
                cand.get("candidate_name"), exc,
            )
            scored.append({
                **cand,
                "final_score": 0.0,
                "weighted_sum": 0.0,
                "genai_bonus_applied": 0.0,
                "scoring_mode": mode,
                "weight_breakdown": weights,
                "component_scores": {},
                "recommendation": "Error — Manual Review Required",
            })

    # ── Phase 2: sort pool → assign rank + pool-relative label ───────────────
    # This is the comparative step — all candidates must be scored first.
    scored.sort(key=lambda c: c["final_score"], reverse=True)
    pool_size = len(scored)
    top_score = scored[0]["final_score"] if scored else 0.0

    for rank, cand in enumerate(scored, start=1):
        cand["rank_in_pool"] = rank
        cand["total_in_pool"] = pool_size
        cand["top_score"] = top_score
        cand["score_gap_to_top"] = round(top_score - cand["final_score"], 2)
        cand["recommendation"] = _pool_relative_label(
            rank=rank,
            score=cand["final_score"],
            top_score=top_score,
            pool_size=pool_size,
        )
        cand["absolute_tier"] = _absolute_tier(cand["final_score"])

    logger.info(
        "[final_ranker] Ranked %d candidates (%s). Top score: %.1f — labels: %s",
        pool_size,
        mode,
        top_score,
        [c["recommendation"] for c in scored],
    )
    return {**state, "ranked_candidates": scored}


# ─────────────────────────────────────────────────────────────────────────────
# Node 6 — Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _format_ai_insights(cand: dict) -> str:
    """
    Build a structured ai_insights string that captures the full scoring
    narrative: mode, score breakdown, GenAI evidence, and the LLM's
    technical depth notes.  Stored in semantic_analysis_reports.ai_insights.
    """
    det = cand.get("det_scores", {})
    llm = cand.get("llm_scores", {})
    genai = cand.get("genai_data", {})
    weights = cand.get("weight_breakdown", {})
    mode = cand.get("scoring_mode", "N/A")
    rank = cand.get("rank_in_pool", "?")
    total = cand.get("total_in_pool", "?")
    top = cand.get("top_score", "?")
    gap = cand.get("score_gap_to_top", 0)
    fixes = cand.get("applied_fixes", [])

    def w(key: str) -> str:
        return f"{weights.get(key, 0) * 100:.0f}%"

    cs = cand.get("component_scores", {})
    proj_depth = cs.get("project_depth", llm.get("project_depth", 0))

    breakdown = (
        f"  • Technical Match  ({w('technical_match')} weight): {cs.get('technical_match', det.get('technical_match', 0)):.0f}/100\n"
        f"  • Project Depth    ({w('project_depth')} weight): {proj_depth:.0f}/100\n"
        f"  • Education Fit    ({w('education_fit')} weight): {cs.get('education_fit', det.get('education_fit', 0)):.0f}/100\n"
        f"  • Experience Quality ({w('experience_quality')} weight): {cs.get('experience_quality', det.get('experience_quality', 0)):.0f}/100\n"
        f"  • Soft Skills      ({w('soft_skills')} weight): {cs.get('soft_skills', det.get('soft_skills', 0)):.0f}/100\n"
        f"  • Training Score   ({w('training_score')} weight): {cs.get('training_score', det.get('training_score', 0)):.0f}/100\n"
        f"  • Nice-to-Have     ({w('nice_to_have')} weight): {cs.get('nice_to_have', det.get('nice_to_have', 0)):.0f}/100\n"
        f"  • GenAI Bonus: +{genai.get('bonus', 0):.0f}pt "
        f"(context: {genai.get('context', 'none')}, "
        f"terms: {', '.join(genai.get('evidence', [])) or 'none found'})"
    )

    fixes_text = "\n  ".join(fixes) if fixes else "None"

    return (
        f"Scoring Mode: {mode}\n"
        f"Pool Position: #{rank} of {total} candidates\n"
        f"Score vs Top Candidate: {cand.get('final_score')} vs {top} "
        f"(gap: {gap:.1f} pts)\n"
        f"\nScore Breakdown:\n{breakdown}\n"
        f"\nApplied Fixes:\n  {fixes_text}\n"
        f"\nGPT-4o-mini Technical Notes:\n{llm.get('llm_summary', 'Not available')}"
    )


def _format_recommendation_summary(cand: dict) -> str:
    """
    Build the recommendation_summary string: pool position, relative label,
    absolute tier, score rationale, and interview questions.
    Stored in semantic_analysis_reports.recommendation_summary.
    """
    llm = cand.get("llm_scores", {})
    questions = llm.get("interview_questions", [])
    q_text = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(questions))

    return (
        f"Pool Label: {cand.get('recommendation', 'N/A')} "
        f"(Absolute tier: {cand.get('absolute_tier', 'N/A')})\n"
        f"Rank #{cand.get('rank_in_pool')} of {cand.get('total_in_pool')} — "
        f"Score: {cand.get('final_score')}/100 "
        f"(weighted sum: {cand.get('weighted_sum')}, "
        f"GenAI bonus: +{cand.get('genai_bonus_applied', 0):.0f})\n"
        f"Score gap to top candidate: {cand.get('score_gap_to_top', 0):.1f} pts\n"
        f"\nSuggested Interview Questions:\n{q_text or '  (none generated)'}"
    )


def persistence_node(state: RankingState) -> RankingState:
    """
    Atomically upserts one SemanticAnalysisReport per application and
    replaces all SemanticMatchedSkill rows.

    Fields written to semantic_analysis_reports:
      match_percentage      ← final weighted score (0–100)
      ai_insights           ← mode, score breakdown, GenAI evidence, LLM notes
      recommendation_summary← pool label, rank, gap to top, interview questions
      strengths             ← bullet-point list from GPT-4o-mini
      weaknesses            ← concerns from GPT-4o-mini
      rank_in_pool          ← comparative rank across the entire applicant pool

    SemanticMatchedSkill rows:
      skill_name / match_type — one row per JD skill found in the candidate CV
      (exact = in skills list, semantic = via synonym, text = raw text scan)

    Re-ranking atomicity:
      All reports are re-written in a single transaction.  A second call
      (e.g. after the deadline is extended and a new CV arrives) will update
      rank_in_pool for every existing candidate automatically.
    """
    ranked = state.get("ranked_candidates", [])
    if not ranked:
        logger.info("[persistence] No candidates to persist.")
        return state

    db = SessionLocal()
    saved, failed = 0, 0
    try:
        for cand in ranked:
            application_id = cand.get("application_id")
            if not application_id:
                continue
            try:
                llm = cand.get("llm_scores", {})

                ai_insights = _format_ai_insights(cand)
                recommendation_summary = _format_recommendation_summary(cand)

                strengths_text = "\n".join(
                    f"• {s}" for s in llm.get("strengths", [])
                ) or "Not assessed"
                weaknesses_text = "\n".join(
                    f"• {c}" for c in llm.get("concerns", [])
                ) or "Not assessed"

                # ── Upsert SemanticAnalysisReport ─────────────────────────────
                existing: Optional[SemanticAnalysisReport] = (
                    db.query(SemanticAnalysisReport)
                    .filter(SemanticAnalysisReport.application_id == application_id)
                    .first()
                )

                if existing:
                    existing.match_percentage = cand.get("final_score")
                    existing.ai_insights = ai_insights
                    existing.recommendation_summary = recommendation_summary
                    existing.strengths = strengths_text
                    existing.weaknesses = weaknesses_text
                    existing.rank_in_pool = cand.get("rank_in_pool")
                    report = existing
                else:
                    report = SemanticAnalysisReport(
                        application_id=application_id,
                        match_percentage=cand.get("final_score"),
                        ai_insights=ai_insights,
                        recommendation_summary=recommendation_summary,
                        strengths=strengths_text,
                        weaknesses=weaknesses_text,
                        rank_in_pool=cand.get("rank_in_pool"),
                    )
                    db.add(report)

                # Flush so report_id is populated for both new and existing rows
                db.flush()

                # ── Replace SemanticMatchedSkill rows ─────────────────────────
                # Always delete first (handles both new-record and re-ranking cases)
                db.query(SemanticMatchedSkill).filter(
                    SemanticMatchedSkill.report_id == report.report_id
                ).delete(synchronize_session=False)

                for matched in cand.get("matched_skills", []):
                    db.add(SemanticMatchedSkill(
                        report_id=report.report_id,
                        skill_name=matched["skill"],
                        match_type=matched.get("match_type", "exact"),
                    ))

                saved += 1

            except Exception as exc:
                logger.warning(
                    "[persistence] Failed to save application %d — %s",
                    application_id, exc,
                )
                failed += 1

        db.commit()
        logger.info(
            "[persistence] Saved %d reports, %d failed. "
            "Pool: %d candidates re-ranked.",
            saved, failed, len(ranked),
        )

    except Exception as exc:
        db.rollback()
        logger.exception("[persistence] DB transaction error — rolling back all writes.")
        return {**state, "error": f"Persistence failed: {exc}"}
    finally:
        db.close()

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder + entry point
# ─────────────────────────────────────────────────────────────────────────────

def _route_after_context(state: RankingState) -> str:
    if state.get("error"):
        return "abort"
    if not state.get("candidates_data"):
        return "abort"
    return "continue"


def _route_after_scoring(state: RankingState) -> str:
    return "abort" if state.get("error") else "continue"


def _build_ranking_graph():
    graph = StateGraph(RankingState)

    graph.add_node("context_gatherer", context_gatherer_node)
    graph.add_node("deterministic_scoring", deterministic_scoring_node)
    graph.add_node("llm_qualitative", llm_qualitative_node)
    graph.add_node("genai_validator", genai_validator_node)
    graph.add_node("final_ranker", final_ranker_node)
    graph.add_node("persistence", persistence_node)

    graph.set_entry_point("context_gatherer")

    graph.add_conditional_edges(
        "context_gatherer",
        _route_after_context,
        {"continue": "deterministic_scoring", "abort": END},
    )
    graph.add_conditional_edges(
        "deterministic_scoring",
        _route_after_scoring,
        {"continue": "llm_qualitative", "abort": END},
    )
    graph.add_edge("llm_qualitative", "genai_validator")
    graph.add_edge("genai_validator", "final_ranker")
    graph.add_edge("final_ranker", "persistence")
    graph.add_edge("persistence", END)

    return graph.compile()


_compiled_graph = None


def run_ranking_graph(requisition_id: int) -> RankingState:
    """
    Entry point.  Builds (or reuses) the compiled graph and executes it
    for the given requisition.  Returns the final state dict.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_ranking_graph()

    initial: RankingState = {
        "requisition_id": requisition_id,
        "posting_id": None,
        "jd_data": {},
        "candidates_data": [],
        "scored_candidates": [],
        "llm_results": [],
        "validated_candidates": [],
        "ranked_candidates": [],
        "error": None,
    }

    logger.info("[ranking_graph] Starting ranking workflow for requisition %d.", requisition_id)
    result: RankingState = _compiled_graph.invoke(initial)
    logger.info(
        "[ranking_graph] Workflow complete for requisition %d — "
        "%d candidates ranked, error=%s",
        requisition_id,
        len(result.get("ranked_candidates", [])),
        result.get("error"),
    )
    return result

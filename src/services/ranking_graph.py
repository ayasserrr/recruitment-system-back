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
from pathlib import Path
from typing import Optional, TypedDict

import asyncio
import httpx
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import joinedload

# Optional: embedding_service provides CE reranking + hybrid scoring.
# Imported lazily-guarded so the pipeline still runs without sentence-transformers.
try:
    from services.embedding_service import (  # type: ignore
        rerank as _ce_rerank,
        hybrid_score as _hybrid_score,
        sigmoid as _ce_sigmoid,
        encode_jd as _encode_jd,
        encode_cvs_batch as _encode_cvs_batch,
        validate_embedding_quality as _validate_embedding_quality,
    )
    _EMBEDDING_SERVICE_AVAILABLE = True
except ImportError:
    _EMBEDDING_SERVICE_AVAILABLE = False
    logger_import = logging.getLogger(__name__)
    logger_import.warning(
        "[ranking_graph] embedding_service not importable — "
        "CE reranking disabled; pipeline uses deterministic scoring only."
    )

# Optional: qdrant_service provides persistent Top-K retrieval per JD.
try:
    from services.qdrant_service import (  # type: ignore
        upsert_cvs_batch as _qdrant_upsert_cvs_batch,
        search_for_jd as _qdrant_search_for_jd,
        delete_jd_vectors as _qdrant_delete_jd_vectors,
    )
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False
    logger_import_qdrant = logging.getLogger(__name__)
    logger_import_qdrant.warning(
        "[ranking_graph] qdrant_service not importable — "
        "vector retrieval disabled; pipeline ranks full pool."
    )

# Optional: xai_service provides SHAP (AutoTokenizer masker) + HR explanation card.
# Imported lazily-guarded so the pipeline still runs without shap/transformers.
try:
    from services.xai_service import (  # type: ignore
        build_shap_explainer as _build_shap_explainer,
        generate_explanations_for_pool as _generate_explanations_for_pool,
        SHAP_K as _XAI_SHAP_K,
    )
    _XAI_SERVICE_AVAILABLE = True
except ImportError:
    _XAI_SERVICE_AVAILABLE = False
    logger_import_xai = logging.getLogger(__name__)
    logger_import_xai.warning(
        "[ranking_graph] xai_service not importable — "
        "HR explanation cards + SHAP disabled; pipeline persists scoring only."
    )

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


# ── Output artifacts (idempotent per requisition_id) ─────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "output"


def _cleanup_stale_artifacts(jr_id: int) -> None:
    """Delete any existing output artifacts for this requisition from output/."""
    if not _OUTPUT_DIR.exists():
        return
    prefixes = (f"hr_report_jr{jr_id}_", f"results_jr{jr_id}_")
    removed = 0
    for f in _OUTPUT_DIR.iterdir():
        if f.is_file() and f.name.startswith(prefixes):
            try:
                f.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("[ranking_graph] Could not delete stale file %s: %s", f, exc)
    if removed:
        logger.info("[ranking_graph] Removed %d stale artifact(s) for jr_id=%d.", removed, jr_id)


def _write_results_json(jr_id: int, jd: dict, ranked: list[dict]) -> str:
    """Write ranking results JSON to output/ and return absolute path string."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = _OUTPUT_DIR / f"results_jr{jr_id}_{timestamp}.json"

    serialisable = []
    for c in ranked:
        serialisable.append({
            "application_id": c.get("application_id"),
            "candidate_id": c.get("candidate_id"),
            "candidate_name": c.get("candidate_name"),
            "email": c.get("email"),
            "rank_in_pool": c.get("rank_in_pool"),
            "total_in_pool": c.get("total_in_pool"),
            "final_score": float(c.get("final_score")) if c.get("final_score") is not None else None,
            "recommendation": c.get("recommendation"),
            "ce_logit": c.get("ce_logit"),
            "ce_prob": c.get("ce_prob"),
            "hybrid_score": c.get("hybrid_score"),
            "hr_explanation_json": c.get("hr_explanation_json"),
        })

    payload = {
        "jr_id": jr_id,
        "generated_at": datetime.utcnow().isoformat(),
        "job_title": jd.get("job_title"),
        "ranked_candidates": serialisable,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    logger.info("[ranking_graph] results.json written: %s", out_path)
    return str(out_path)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"

# ── Label thresholds (Gold Standard FIT_THRESHOLD / PARTIAL_THRESHOLD) ────────
FIT_THRESHOLD     = 0.80   # skill overlap ≥ 80% → good_fit
PARTIAL_THRESHOLD = 0.40   # skill overlap ≥ 40% → partial_fit; < 40% → no_fit

# ── Pipeline configuration (Gold Standard TOP_K / SHAP_K) ─────────────────────
TOP_K  = 20  # Bi-Encoder candidates passed to Cross-Encoder
SHAP_K = 0   # Disabled: SHAP is too slow on CPU. Set to 5 to re-enable for top-5 candidates.

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
        "convolutional", "yolo", "yolov5", "yolov7", "yolov8", "yolov9",
        "ultralytics", "opencv", "cv2", "visual recognition",
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
    # Extended skill groups derived from Gold Standard notebook
    "mongodb": ["mongodb", "mongo", "mongodb atlas", "nosql", "document database", "document store"],
    "redis": ["redis", "redis cache", "caching layer", "in-memory cache", "memcached"],
    "nodejs": ["node.js", "nodejs", "node", "express.js", "express", "deno"],
    "graphql": ["graphql", "apollo", "hasura", "graphql api", "graphql schema"],
    "microservices": ["microservice", "microservices", "service oriented", "service mesh", "service-oriented"],
    "time_series": [
        "time series", "time-series", "forecasting", "arima", "temporal data",
        "lstm", "sequence modeling", "trend analysis", "anomaly detection time",
    ],
    "web_scraping": [
        "web scraping", "beautifulsoup", "selenium", "scrapy", "crawling",
        "data extraction", "web crawler", "puppeteer", "playwright",
    ],
    "streaming": [
        "kafka", "spark streaming", "flink", "rabbitmq", "event streaming",
        "message queue", "pub/sub", "kinesis", "event-driven",
    ],
    "system_design": [
        "system design", "architecture design", "high level design", "hld",
        "system architecture", "designed architecture", "software architecture",
    ],
    "leadership": [
        "led team", "managed team", "team lead", "tech lead", "engineering lead",
        "mentored", "supervised team", "team of", "led the development",
    ],
    "security": [
        "jwt", "oauth", "authentication system", "authorization", "role-based access",
        "rbac", "encryption", "secure api", "https", "ssl", "tls",
    ],
    "git": ["git", "github", "gitlab", "bitbucket", "version control", "source control"],
    "agile": ["agile", "scrum", "sprint", "kanban", "jira", "confluence", "standup", "agile workflow"],
    "recommendation_systems": [
        "recommendation system", "recommender system", "collaborative filtering",
        "content-based filtering", "matrix factorization",
    ],
    "workflow_orchestration": [
        "airflow", "luigi", "prefect", "dbt", "workflow orchestration",
        "pipeline orchestration", "celery", "task queue",
    ],
    "javascript": [
        "javascript", "js", "typescript", "ts", "react", "vue", "angular",
        "nextjs", "next.js", "svelte", "frontend",
    ],
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

# ── Implicit skill inference patterns (Gold Standard PROJECT_SKILL_PATTERNS) ──
# 36 regex patterns across 10 categories that detect skills demonstrated in
# project/experience text even when not explicitly listed in the CV skills section.
_IMPLICIT_SKILL_PATTERNS: dict[str, list[str]] = {
    # APIs & Backend
    "rest_api": [
        r"rest[\s_]?api|restful|http[\s_]endpoint",
        r"built[\s_]?api|exposed[\s_]?as[\s_]?api",
    ],
    "fastapi": [r"fastapi|fast[\s_]api"],
    "flask": [r"flask[\s_]app|using[\s_]flask|built[\s_]with[\s_]flask"],
    "graphql": [r"\bgraphql\b"],
    "microservices": [r"microservice|service[\s_]oriented[\s_]arch"],
    # ML / Deep Learning
    "deep_learning": [
        r"neural[\s_]network|deep[\s_]learning|backprop",
        r"train\w*[\s_]model|model[\s_]train",
    ],
    "fine_tuning": [
        r"fine[\s_]?tun|transfer[\s_]learning",
        r"\blora\b|\bqlora\b|\bpeft\b",
    ],
    "transformers": [
        r"transformer|bert|gpt[\s_]model|attention[\s_]mech",
    ],
    "computer_vision_impl": [
        r"cnn|convolutional|image[\s_]classif|object[\s_]detect",
        r"yolov[5-9]|ultralytics",
        r"mediapipe|keypoint[\s_]classif|point[\s_]history",
    ],
    "recommendation_systems": [
        r"recommend\w*[\s_]system|collaborative[\s_]filter",
    ],
    "time_series": [
        r"time[\s_]series|forecasting|lstm|arima",
    ],
    # NLP
    "nlp_impl": [
        r"text[\s_]classif|sentiment[\s_]anal|named[\s_]entity|nlp[\s_]pipeline",
    ],
    "chatbot": [r"chatbot|conversational[\s_]ai|dialog[\s_]system"],
    "rag_impl": [r"rag|retrieval[\s_]augmented"],
    "langchain_impl": [r"langchain|langgraph"],
    "huggingface_impl": [r"hugging[\s_]face|huggingface[\s_]model"],
    # Data Engineering
    "data_pipeline": [
        r"data[\s_]pipeline|etl|data[\s_]engineer",
    ],
    "streaming": [r"kafka|spark[\s_]stream|flink|rabbitmq"],
    "workflow_orchestration_impl": [r"airflow|luigi|prefect|dbt\b"],
    "web_scraping_impl": [r"web[\s_]scraping|beautifulsoup|selenium|scrapy"],
    # Vector / Search
    "vector_database_impl": [
        r"vector[\s_]database|embedding[\s_]store|faiss|pinecone|qdrant|weaviate",
    ],
    "semantic_search": [r"similarity[\s_]search|semantic[\s_]search"],
    # Cloud / DevOps
    "aws_impl": [r"deploy\w*[\s_]on[\s_]aws|ec2|s3[\s_]bucket|lambda[\s_]function"],
    "gcp_impl": [r"deploy\w*[\s_]on[\s_]gcp|google[\s_]cloud[\s_]run|bigquery"],
    "azure_impl": [r"deploy\w*[\s_]on[\s_]azure|azure[\s_]func"],
    "docker_impl": [r"docker[\s_]image|dockerfile|containeriz|dockerized"],
    "kubernetes_impl": [r"kubernetes|k8s|helm[\s_]chart"],
    "cicd": [r"ci[\s_]?/[\s_]?cd|github[\s_]actions|jenkins|gitlab[\s_]ci"],
    "iac": [r"terraform|infra[\s_]as[\s_]code|ansible"],
    # Databases
    "sql_impl": [r"designed[\s_]schema|relational[\s_]db|sql[\s_]query"],
    "mongodb_impl": [r"mongodb[\s_]atlas|nosql[\s_]database"],
    "redis_impl": [r"redis[\s_]cache|caching[\s_]layer"],
    # MLOps
    "mlops_impl": [
        r"mlflow|model[\s_]registry|experiment[\s_]tracking",
        r"model[\s_]serv|model[\s_]deploy|serving[\s_]layer",
        r"model[\s_]monitor|drift[\s_]detect|automated[\s_]retrain",
    ],
    # Architecture / Leadership
    "system_design_impl": [
        r"architected[\s_]the|designed[\s_]the[\s_]system|system[\s_]design",
    ],
    "leadership_impl": [r"led[\s_]team|managed[\s_]team|team[\s_]of[\s_]\d"],
    "agile_impl": [r"agile[\s_]workflow|scrum[\s_]team|sprint[\s_]planning"],
    # Security
    "security_impl": [r"jwt|oauth|authentication[\s_]system|role[\s_]based[\s_]access"],
}

# ── Implicit skill confidence weights (audit wiring) ─────────────────────────
# Each implicit skill category gets a heuristic confidence weight representing
# how reliably the regex implies hands-on experience (vs keyword noise).
# Only matches with weight >= _IMPLICIT_CONFIDENCE_THRESHOLD are returned.
_IMPLICIT_SKILL_CONFIDENCE: dict[str, float] = {
    # Backend frameworks: context-heavy patterns → high confidence
    "fastapi": 0.75,
    "flask": 0.75,
    "graphql": 0.70,
    "rest_api": 0.65,
    "microservices": 0.60,
    # ML implementation signals
    "deep_learning": 0.70,
    "fine_tuning": 0.75,
    "transformers": 0.65,
    "computer_vision_impl": 0.70,
    "nlp_impl": 0.65,
    "rag_impl": 0.75,
    "langchain_impl": 0.70,
    "huggingface_impl": 0.65,
    # Data engineering / infra
    "data_pipeline": 0.60,
    "streaming": 0.65,
    "workflow_orchestration_impl": 0.65,
    "web_scraping_impl": 0.60,
    # Vector/search
    "vector_database_impl": 0.70,
    "semantic_search": 0.65,
    # DevOps/cloud
    "docker_impl": 0.70,
    "kubernetes_impl": 0.70,
    "cicd": 0.65,
    "iac": 0.65,
    "aws_impl": 0.65,
    "gcp_impl": 0.65,
    "azure_impl": 0.65,
    # Databases
    "sql_impl": 0.60,
    "mongodb_impl": 0.60,
    "redis_impl": 0.60,
    # MLOps + architecture
    "mlops_impl": 0.70,
    "system_design_impl": 0.60,
    "leadership_impl": 0.60,
    "agile_impl": 0.60,
    "security_impl": 0.60,
    # Other categories default via .get(..., 0.60)
}

_IMPLICIT_CONFIDENCE_THRESHOLD = 0.60

# ── Project complexity dimensions (Gold Standard PROJECT_WEIGHT_KEYWORDS) ──────
# Weighted regex dimensions for deterministic project quality scoring.
# Weights sum to 1.0. Each dimension fires if ANY pattern matches.
_PROJECT_COMPLEXITY_DIMENSIONS: dict[str, dict] = {
    "scale": {
        "weight": 0.30,
        "patterns": [
            r"\d+\s*[km]\+?\s*user",
            r"million[\s_]user",
            r"high[\s_]traffic",
            r"large[\s_]scale|large-scale",
            r"\d+\s*tb\b",
            r"\d+k\+?\s*request",
            r"production[\s_]system",
            r"enterprise[\s_]level",
        ],
    },
    "complexity": {
        "weight": 0.25,
        "patterns": [
            r"distributed[\s_]system",
            r"real[\s-]?time",
            r"custom[\s_]algorithm",
            r"from[\s_]scratch",
            r"optimiz\w+[\s_](?:algorithm|query|model)",
            r"novel[\s_]approach",
            r"research[\s_]paper",
            r"multi[\s-]?modal",
            r"end[\s-]to[\s-]end",
            r"semantic[\s_](?:cv|analysis|search|matching)",
            r"multi[\s-]?agent|multi[\s-]?step",
        ],
    },
    "impact": {
        "weight": 0.25,
        "patterns": [
            r"\d+\s*%\s*(?:improv|reduc|increas|accur|faster|speedup)",
            r"(?:open[\s_]?source|github)[\s\w]*\d+[\s_]star",
            r"\baward\b",
            r"\bpublish\w*\b",
            r"used[\s_](?:in|by)[\s_]production",
            r"revenue[\s_]impact",
            r"cost[\s_]saving",
            r"automated[\s_](?:technical|behavioral|assessment|ranking)",
        ],
    },
    "ownership": {
        "weight": 0.20,
        "patterns": [
            r"(?:sole|solo|independently)[\s_]developed",
            r"architected[\s_](?:and|the)",
            r"built[\s_](?:and[\s_]deployed|from[\s_]scratch)",
            r"designed[\s_]and[\s_](?:developed|implemented)",
            r"led[\s_](?:development|team|the)",
            r"(?:built|developed|implemented)[\s_](?:a[\s_])?(?:deep|cnn|yolo|mediapipe|intelligent)",
        ],
    },
}

# ── Text normalization constants ───────────────────────────────────────────────
_NOISE_PATTERN = re.compile(r"[^\w\s,./+#@-]")
_MULTI_SPACE   = re.compile(r"\s+")

# ── Date range extraction for experience fallback ──────────────────────────────
_DATE_RANGE_PATTERN = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*)?"
    r"(\d{4})\s*[-–—]\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*)?"
    r"(\d{4}|present|current|now|till\s*date|ongoing)",
    re.IGNORECASE,
)
_MONTHS_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
# ── Experience line exclusion patterns (Gold Standard 3-pattern approach) ──────
# Split into three groups to avoid false-positives on lines like:
#   "Senior ML Engineer at University Hospital 2022 – 2024"  ← KEEP (work experience)
#   "Internship at XYZ 2023 – 2024"                          ← SKIP (not full-time)
#   "Bachelor of Science, Cairo University 2018 – 2022"      ← SKIP (education)

# 1. Strong signal of non-permanent work → always skip
_WORK_INTERNSHIP_KW = re.compile(
    r"\b(intern|internship|trainee|training|volunteer|part[\s-]?time|freelance"
    r"|apprentice|attachment|practicum|summer\s+program)\b",
    re.IGNORECASE,
)
# 2. Education lines → skip UNLESS a job-title indicator is also present
_EDUCATION_LINE_KW = re.compile(
    r"\b(bachelor|master|phd|university|college|institute|school"
    r"|expected[\s_]graduation|graduation|coursework|degree)\b",
    re.IGNORECASE,
)
# 3. Job-title indicators — if present on an education line, it's actually work experience
_JOB_TITLE_INDICATOR = re.compile(
    r"\b(engineer|developer|scientist|analyst|manager|lead|director"
    r"|consultant|architect|researcher|specialist|officer|head|senior|junior)\b",
    re.IGNORECASE,
)

# ── Skill alias map (alias → canonical) for normalization ─────────────────────
# Derived from Gold Standard SKILL_ALIASES (193+ entries condensed to key groups)
_SKILL_ALIASES: dict[str, str] = {
    # Python ecosystem
    "python3": "python", "py": "python", "pythonic": "python",
    # JavaScript
    "js": "javascript",
    # TypeScript is a DISTINCT skill from JavaScript — DO NOT collapse.
    # A JD requiring TypeScript should not be satisfied by a plain JS CV.
    "ts": "typescript",        # "ts" → typescript (not javascript)
    # "typescript": "javascript"  ← REMOVED (audit fix #2)
    "node.js": "nodejs",
    # "node": "nodejs" ← REMOVED: too broad, matches "node" in "decision node",
    #   "knowledge graph node", etc. Use only "node.js" (specific enough).
    # React / Vue / Angular — distinct first-class canonicals
    "reactjs": "react", "react.js": "react",
    "vuejs": "vue",    "vue.js": "vue",
    "angularjs": "angular",
    # ML Frameworks
    "tf": "tensorflow", "tf2": "tensorflow", "keras": "tensorflow",
    "torch": "pytorch",
    # Scikit-learn
    "sklearn": "scikit-learn", "scikit": "scikit-learn",
    "scikit learn": "scikit-learn",
    # Cloud
    "amazon web services": "aws", "ec2": "aws", "s3": "aws",
    "google cloud": "gcp", "google cloud platform": "gcp", "bigquery": "gcp",
    "microsoft azure": "azure", "ms azure": "azure",
    # Computer Vision / YOLO
    # NOTE: "cv" alias INTENTIONALLY OMITTED — causes false-positive CV (curriculum vitae) matches
    "cv2": "opencv",
    "computervision": "computer vision", "image processing": "computer vision",
    "yolov5": "yolo", "yolov7": "yolo", "yolov8": "yolo", "yolov9": "yolo",
    "ultralytics": "yolo",
    "cnn": "convolutional neural networks",
    "cnns": "convolutional neural networks",
    "convolutional neural network": "convolutional neural networks",
    "object detection": "object detection",
    "image classification": "image classification",
    # NLP / LLM
    "natural language processing": "nlp",
    "llm": "large language models", "llms": "large language models",
    "large language model": "large language models",
    "huggingface": "huggingface",
    "hugging face": "huggingface",
    "transformers": "transformers", "transformer": "transformers",
    "retrieval augmented generation": "rag",
    "gpt": "gpt", "llama": "llama", "gemini": "gemini",
    # Databases
    "mysql": "sql", "postgresql": "sql", "postgres": "sql", "sqlite": "sql",
    "mongo": "mongodb", "mongodb atlas": "mongodb",
    # DevOps
    "k8s": "kubernetes",
    "ci/cd": "ci/cd", "cicd": "ci/cd",
    "docker": "docker",
    # Data Engineering — now in alias map (were only in _SKILL_SYNONYMS before)
    "apache spark": "spark", "pyspark": "spark",
    "apache kafka": "kafka",
    "apache airflow": "airflow",
    "dbt core": "dbt",
    # General
    "rest api": "rest api", "restful api": "rest api", "restful": "rest api",
    "api": "api",
    "github": "git", "gitlab": "git", "bitbucket": "git",
    "version control": "git",
    "dl": "deep learning",
    "ml": "machine learning",
    "oop": "object oriented programming",
    "object-oriented programming": "object oriented programming",
    # Data tools — own canonical
    "pandas": "pandas", "numpy": "numpy",
    "matplotlib": "matplotlib", "seaborn": "seaborn", "jupyter": "jupyter",
    "faiss": "faiss", "pinecone": "pinecone", "weaviate": "weaviate",
    "vector database": "vector databases", "vector databases": "vector databases",
    "fastapi": "fastapi", "fast api": "fastapi",
    "streamlit": "streamlit", "mlflow": "mlflow",
    # Data Science semantic synonyms
    "data analytics": "data analysis",
    "statistical analysis": "data science",
    "data preprocessing": "data science",
    "exploratory data analysis": "data science",
    "eda": "data science",
    "feature engineering": "data science",
    "model training": "data science",
    # Agile / workflow
    "agile workflow": "agile", "scrum team": "agile", "scrum": "scrum",
    "sprint planning": "agile",
}

# ── Short-alias guard (audit fix #2) ──────────────────────────────────────────
# Single-character or ambiguous aliases must NEVER be matched via free-text
# word-boundary regex (\b) because they cause false positives:
#   "r" matches the letter r in countless words after lowercase normalization.
#   "c" similarly.
#   "go" matches "go" in "going", "algorithm", "cargo" etc.
# These skills are only matched when they appear as an explicit, standalone
# token surrounded by whitespace/punctuation — NOT inside another word.
_SHORT_SKILL_EXACT_ONLY: frozenset[str] = frozenset({"r", "c", "go", "c#", "c++"})


def _skill_in_text_safe(skill: str, lower_text: str) -> bool:
    """
    Safe skill presence check that handles short ambiguous aliases.
    For skills in _SHORT_SKILL_EXACT_ONLY: require surrounding non-alpha chars.
    For all other skills: standard word-boundary regex.
    """
    if skill in _SHORT_SKILL_EXACT_ONLY:
        # Match only when the skill is a standalone token (not inside a word)
        import re as _re
        return bool(_re.search(
            r"(?<![a-z0-9])" + _re.escape(skill) + r"(?![a-z0-9])",
            lower_text,
        ))
    return bool(re.search(r"\b" + re.escape(skill) + r"\b", lower_text))


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
    Normalizes via _SKILL_ALIASES before matching (Gold Standard approach) so
    aliases like 'ml' match 'machine learning', 'yolov8' matches 'yolo', etc.
    """
    req_canonical = normalize_skill(required_skill)
    req_lower     = req_canonical

    # Normalize candidate skills to canonical forms too
    cand_canonical = [normalize_skill(s) for s in candidate_skills]
    cand_lower     = [s.lower() for s in candidate_skills]

    # 1. Exact canonical match
    if any(req_lower in s for s in cand_canonical) or any(req_lower in s for s in cand_lower):
        return True, "exact"

    text_lower = _normalize_text(raw_text)

    # 2. Synonym expansion through canonical groups
    for canonical, synonyms in _SKILL_SYNONYMS.items():
        if req_lower == canonical or req_lower in synonyms or required_skill.lower() in synonyms:
            for term in [canonical] + synonyms:
                if any(term in s for s in cand_canonical) or any(term in s for s in cand_lower):
                    return True, "semantic"
                if term in text_lower:
                    return True, "text"

    # 3. Direct substring in normalized CV text
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
    # Delegate to the short-alias–aware helper so single-char skills like
    # "r", "c", "go" don't generate false positives inside longer words.
    return _skill_in_text_safe(term, text)


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


def _normalize_text(text: str) -> str:
    """Lowercase → remove noise punctuation → collapse whitespace (Gold Standard)."""
    text = text.lower()
    text = _NOISE_PATTERN.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


def normalize_skill(skill: str) -> str:
    """
    Map any skill alias to its canonical form using _SKILL_ALIASES.
    Falls back to lowercase-stripped input if no alias is found.
    """
    key = skill.lower().strip()
    return _SKILL_ALIASES.get(key, key)


def _extract_implicit_skills(exp_text: str, proj_text: str) -> list[tuple[str, str]]:
    """
    Scan raw experience + project text for skills demonstrated in context
    but not explicitly listed in the CV skills section.

    Uses 36 regex patterns across 10 categories (Gold Standard PROJECT_SKILL_PATTERNS).
    Returns [(canonical_skill, evidence_snippet)] — one match per category.
    """
    combined = _normalize_text(f"{exp_text} {proj_text}")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for canonical, patterns in _IMPLICIT_SKILL_PATTERNS.items():
        confidence = _IMPLICIT_SKILL_CONFIDENCE.get(canonical, 0.60)
        if confidence < _IMPLICIT_CONFIDENCE_THRESHOLD:
            continue

        for pat in patterns:
            m = re.search(pat, combined)
            if not m or canonical in seen:
                continue

            start = max(0, m.start() - 35)
            end = min(len(combined), m.end() + 50)
            snippet = combined[start:end].replace("\n", " ").strip()
            found.append((canonical, f"(confidence={confidence:.2f}) …{snippet}…"))
            seen.add(canonical)
            break

    return found


def _score_project_complexity(project_texts: list[str]) -> tuple[float, list[str]]:
    """
    Weighted regex project quality scoring across 4 dimensions
    (scale 30%, complexity 25%, impact 25%, ownership 20%).
    Derived from Gold Standard PROJECT_WEIGHT_KEYWORDS.

    Returns (total_score 0.0–1.0, dimension_labels_with_contribution).
    """
    combined = _normalize_text(" ".join(project_texts))
    dimensions_hit: list[str] = []
    total = 0.0
    for dim, cfg in _PROJECT_COMPLEXITY_DIMENSIONS.items():
        if any(re.search(pat, combined) for pat in cfg["patterns"]):
            weight = cfg["weight"]
            total += weight
            dimensions_hit.append(f"{dim}(+{weight*100:.0f}%)")
    return round(min(total, 1.0), 3), dimensions_hit


def _is_education_line(line: str) -> bool:
    """
    Return True if this line should be excluded from experience counting.
    Gold Standard 3-pattern approach:
      - Internship/volunteer/freelance keywords → always exclude.
      - Education keywords → exclude UNLESS a job-title indicator is also present.
        (prevents skipping "Senior ML Engineer at University Hospital 2022 – 2024")
    """
    if _WORK_INTERNSHIP_KW.search(line):
        return True
    if _EDUCATION_LINE_KW.search(line) and not _JOB_TITLE_INDICATOR.search(line):
        return True
    return False


def _extract_experience_from_text(raw_text: str) -> int:
    """
    Fallback: extract years of full-time work experience from date ranges in CV text.
    Skips internship, education, and volunteer lines using the Gold Standard
    3-pattern approach (_is_education_line).
    Returns 0 if no valid ranges are found (never negative).
    """
    today = date.today()
    total_months = 0
    for line in raw_text.splitlines():
        if _is_education_line(line):
            continue
        for m in _DATE_RANGE_PATTERN.finditer(line):
            start_mo_str = (m.group(1) or "").lower().strip().rstrip(".")[:3]
            start_yr_str = m.group(2)
            end_mo_str   = (m.group(3) or "").lower().strip().rstrip(".")[:3]
            end_yr_str   = m.group(4)
            try:
                start_yr = int(start_yr_str)
                start_mo = _MONTHS_MAP.get(start_mo_str, 1)
                end_str  = end_yr_str.lower().strip()
                if end_str in ("present", "current", "now", "ongoing", "till date"):
                    end_yr, end_mo = today.year, today.month
                else:
                    end_yr = int(end_str)
                    end_mo = _MONTHS_MAP.get(end_mo_str, 12)
                if start_yr < 1990 or end_yr > today.year + 1:
                    continue
                if end_yr < start_yr or (end_yr == start_yr and end_mo < start_mo):
                    continue
                duration = (end_yr - start_yr) * 12 + (end_mo - start_mo)
                if 1 <= duration <= 600:
                    total_months += duration
            except (ValueError, AttributeError):
                continue
    return max(0, round(total_months / 12))


def normalize_skills(skills: list[str]) -> list[str]:
    """
    Normalize a list of skills and deduplicate while preserving order.
    Gold Standard normalize_skills() — applies normalize_skill() to each entry.
    """
    seen: set[str] = set()
    result: list[str] = []
    for s in skills:
        normalized = normalize_skill(s)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def compute_skill_overlap(cv_skills: list[str], jd_required: list[str]) -> float:
    """
    Fraction of JD required skills that appear in the CV (explicit + implicit merged).
    Both lists must already be normalized. Returns 0.0 if jd_required is empty.
    Gold Standard compute_skill_overlap().
    """
    if not jd_required:
        return 0.0
    cv_set = set(cv_skills)
    matched = sum(1 for s in jd_required if s in cv_set)
    return matched / len(jd_required)


def assign_label(
    overlap: float,
    cv_years: int,
    jd_years: int,
    project_score: float = 0.0,
) -> str:
    """
    Map skill overlap + experience + project complexity → fit label.
    Gold Standard assign_label() with project_score boost (v4 — fixed).

    project_score (0.0–1.0) gives a max +10% boost to effective overlap:
      • 72% skills + 0.8 project → 80% effective → good_fit
      • 30% skills + 0.9 project → 39% effective → no_fit  (boost insufficient)
      • 85% skills + under-exp (exp_ratio < 0.5) → partial_fit  (experience gate)

    Returns: "good_fit" | "partial_fit" | "no_fit"
    """
    exp_ratio     = min(cv_years / jd_years, 3.0) if jd_years > 0 else 1.0
    project_boost = project_score * 0.10
    boosted       = min(overlap + project_boost, 1.0)

    if boosted >= FIT_THRESHOLD:
        if exp_ratio < 0.5:
            return "partial_fit"   # strong skills but significantly under-experienced
        return "good_fit"
    elif boosted >= PARTIAL_THRESHOLD:
        return "partial_fit"
    return "no_fit"


def aggregate_project_score(project_texts: list[str]) -> float:
    """
    Average project complexity score across all projects.
    Gold Standard aggregate_project_score() — scores each project independently
    then averages (not concatenating, which would over-count shared keywords).
    Returns 0.0 if no projects.
    """
    if not project_texts:
        return 0.0
    scores = [_score_project_complexity([p])[0] for p in project_texts]
    return round(sum(scores) / len(scores), 3)


def _compute_feature_attribution(cand: dict) -> list[dict]:
    """
    Shapley-style feature attribution for the candidate's final score.

    For a linear weighted-sum model, the Shapley value of each feature is
    weight × score — exactly how SHAP decomposes linear models.
    Produces the same information SHAP would produce without GPU/neural overhead.

    Returns list of dicts sorted descending by contribution_pts (SHAP magnitude).
    """
    cs      = cand.get("component_scores", {})
    det     = cand.get("det_scores", {})
    weights = cand.get("weight_breakdown", {})
    genai   = cand.get("genai_data", {})

    weighted_features = {
        "technical_match":    cs.get("technical_match",    det.get("technical_match",    0.0)),
        "project_depth":      cs.get("project_depth",                                    0.0),
        "education_fit":      cs.get("education_fit",      det.get("education_fit",      0.0)),
        "experience_quality": cs.get("experience_quality", det.get("experience_quality", 0.0)),
        "soft_skills":        cs.get("soft_skills",        det.get("soft_skills",        0.0)),
        "training_score":     cs.get("training_score",     det.get("training_score",     0.0)),
        "nice_to_have":       cs.get("nice_to_have",       det.get("nice_to_have",       0.0)),
    }

    attributions = []
    for feat, score in weighted_features.items():
        w = weights.get(feat, 0.0)
        contribution = round(w * score, 2)
        attributions.append({
            "feature":          feat,
            "score":            round(score, 1),
            "weight_pct":       int(w * 100),
            "contribution_pts": contribution,
            "direction":        "positive" if contribution > 5 else ("neutral" if contribution > 0 else "weak"),
        })

    # Additive bonuses (not weighted — direct contributions)
    attributions.append({
        "feature":          "genai_bonus",
        "score":            round(genai.get("bonus", 0.0), 1),
        "weight_pct":       0,
        "contribution_pts": round(genai.get("bonus", 0.0), 2),
        "direction":        "positive" if genai.get("bonus", 0) > 0 else "neutral",
    })
    attributions.append({
        "feature":          "project_complexity_boost",
        "score":            round(cand.get("project_complexity_score", 0.0) * 100, 1),
        "weight_pct":       0,
        "contribution_pts": round(cand.get("complexity_boost_applied", 0.0), 2),
        "direction":        "positive" if cand.get("complexity_boost_applied", 0) > 0 else "neutral",
    })

    attributions.sort(key=lambda x: x["contribution_pts"], reverse=True)
    return attributions


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
                # ── raw_text: prefer fitz-extracted text stored at upload time ──
                # If extracted_text is present in candidate_cvs (set by apply.py
                # via cv_service.fitz_extract_text), use it directly — it's the
                # authoritative full-text the scoring functions need.
                # Fall back to building from sub-tables only when the CV was
                # uploaded before this feature was added.
                if cv and cv.extracted_text and len(cv.extracted_text.strip()) >= 80:
                    record["raw_text"] = _normalize_text(cv.extracted_text)
                    logger.info(
                        "[context_gatherer] Scored via stored fitz extracted_text "
                        "for cv_id=%d application_id=%d (%d chars).",
                        cv.cv_id, app.application_id, len(cv.extracted_text),
                    )
                else:
                    record["raw_text"] = _build_raw_text(record)
                    if not record["raw_text"].strip():
                        logger.error(
                            "[context_gatherer] EMPTY raw_text for application_id=%d "
                            "(cv_id=%s) — CV was not parsed at upload time. "
                            "Ranking scores will be zero.",
                            app.application_id,
                            cv.cv_id if cv else "None",
                        )
                    else:
                        logger.info(
                            "[context_gatherer] Built raw_text from sub-tables for "
                            "application_id=%d (cv_id=%s, %d chars).",
                            app.application_id,
                            cv.cv_id if cv else "None",
                            len(record["raw_text"]),
                        )

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
    Rule-based scoring for each candidate using the v7.0 algorithm.

    Scores produced (0–100 each):
      • technical_match    — alias-normalized synonym-map + evidence levels + stuffing penalty
      • experience_quality — fulltime years OR internship months (student mode)
                            + date-range fallback if DB years_of_experience is 0
      • education_fit      — degree hierarchy floor + field overlap + AI-field fallback
      • soft_skills        — 5 dimensions via keyword evidence + internship implicit boost
      • training_score     — internship months + training entries + freelance credit
      • nice_to_have       — bonus signals (opencv, yolo, langchain, competitions, OSS)
      • project_complexity — weighted regex (scale 30% / complexity 25% / impact 25% / ownership 20%)
    """
    jd = state["jd_data"]
    required_years: int = jd.get("required_years") or 0
    required_skills: list[dict] = jd.get("required_skills", [])

    candidates_pool: list[dict] = list(state["candidates_data"] or [])

    # ── Phase 2 (retrieval): Qdrant-driven Top-K candidate narrowing ─────────
    # If embeddings + qdrant are available, encode JD + CVs, upsert vectors tagged
    # by jd_id (requisition_id), and retrieve top TOP_K before heavy scoring.
    # This simulates production retrieval and reduces cost for CE/SHAP later.
    if _EMBEDDING_SERVICE_AVAILABLE and _QDRANT_AVAILABLE and candidates_pool:
        try:
            jd_text = (
                jd.get("full_description")
                or jd.get("key_responsibilities")
                or jd.get("job_title")
                or ""
            )
            jd_vec = _encode_jd(jd_text)
            cv_texts = [c.get("raw_text") or "" for c in candidates_pool]
            cv_vecs = _encode_cvs_batch(cv_texts)

            # Sanity floor log (0.55) — helps catch missing query/passsage prefixes.
            _validate_embedding_quality(jd_vec, cv_vecs, threshold=0.55)

            # Keep qdrant collection clean per requisition_id to avoid stale points
            _qdrant_delete_jd_vectors(jd_id=state["requisition_id"])

            points = []
            for cand, vec in zip(candidates_pool, cv_vecs):
                if not vec:
                    continue
                points.append({
                    "cv_id": int(cand.get("application_id") or 0),
                    "jd_id": int(state["requisition_id"]),
                    "vector": vec,
                    "payload": {
                        "candidate_name": cand.get("candidate_name"),
                        "email": cand.get("email"),
                    },
                })

            if points:
                _qdrant_upsert_cvs_batch(points)
                hits = _qdrant_search_for_jd(
                    jd_vector=jd_vec,
                    jd_id=int(state["requisition_id"]),
                    top_k=TOP_K,
                )

                # Map retrieved IDs → similarity score; filter pool to Top-K
                score_by_id = {int(h["cv_id"]): float(h.get("score", 0.0)) for h in hits}
                if score_by_id:
                    narrowed = [
                        {**c, "retrieval_score": round(score_by_id.get(int(c.get("application_id") or 0), 0.0), 4)}
                        for c in candidates_pool
                        if int(c.get("application_id") or 0) in score_by_id
                    ]
                    narrowed.sort(key=lambda c: c.get("retrieval_score", 0.0), reverse=True)
                    candidates_pool = narrowed
                    logger.info(
                        "[deterministic_scoring] Retrieval applied — pool narrowed to %d (TOP_K=%d).",
                        len(candidates_pool), TOP_K,
                    )
        except Exception as exc:
            logger.warning(
                "[deterministic_scoring] Retrieval step failed (%s) — scoring full pool.",
                exc,
            )

    scored: list[dict] = []

    for cand in candidates_pool:
        try:
            raw_text: str = cand.get("raw_text", "")
            # Normalize text before all keyword scanning (Gold Standard)
            cv_lower = _normalize_text(raw_text)
            skill_names: list[str] = [s["name"] for s in cand.get("skills", [])]
            projects    = cand.get("projects", [])
            experiences = cand.get("experiences", [])
            fixes_applied: list[str] = []

            # ── Experience breakdown (fulltime / internship / freelance / training) ──
            exp_breakdown = _calc_experience_breakdown(experiences)
            fulltime_years    = exp_breakdown["fulltime_years"]
            internship_months = exp_breakdown["internship_months"]

            # ── Date-range fallback (Gold Standard) ───────────────────────────
            # If structured DB experience is 0 but the raw text has date ranges,
            # extract years directly from the CV text as a fallback signal.
            db_years = cand.get("years_of_experience") or 0
            if db_years == 0 and fulltime_years == 0:
                text_extracted_years = _extract_experience_from_text(raw_text)
                if text_extracted_years > 0:
                    fulltime_years = text_extracted_years
                    fixes_applied.append(
                        f"[FALLBACK] Experience extracted from CV date ranges: {text_extracted_years}yr"
                    )

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

            # ── Technical match — alias-normalized synonym map + evidence levels
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

            # ── Implicit skill inference (XAI evidence snippets) ──────────────
            exp_text = " ".join(
                f"{e.get('job_title', '')} {e.get('description', '')}"
                for e in experiences
            )
            proj_text_combined = " ".join(
                f"{p.get('project_name', '')} {p.get('description', '')} {p.get('tech_stack', '')}"
                for p in projects
            )
            implicit_skills = _extract_implicit_skills(exp_text, proj_text_combined)

            # ── Project complexity scoring (scale / complexity / impact / ownership) ──
            project_texts = [
                f"{p.get('project_name', '')} {p.get('description', '')} {p.get('tech_stack', '')}"
                for p in projects
            ]
            project_complexity_score, complexity_dimensions = _score_project_complexity(project_texts)

            total_experience_months = int(fulltime_years * 12) + internship_months

            scored.append({
                **cand,
                "total_experience_months":  total_experience_months,
                "fulltime_years":           fulltime_years,
                "internship_months":        internship_months,
                "ai_background_detected":   ai_background,
                "matched_skills":           matched_skills,
                "stuffing_penalty":         stuffing_penalty,
                "applied_fixes":            fixes_applied,
                "implicit_skills":          implicit_skills,
                "project_complexity_score": project_complexity_score,
                "complexity_dimensions":    complexity_dimensions,
                "det_scores": {
                    "technical_match":    round(technical_match, 1),
                    "experience_quality": round(exp_score, 1),
                    "education_fit":      round(edu_score, 1),
                    "soft_skills":        round(soft_score, 1),
                    "training_score":     round(training_score, 1),
                    "nice_to_have":       round(nice_score, 1),
                    "project_complexity": round(project_complexity_score * 100, 1),
                },
            })

        except Exception as exc:
            logger.warning(
                "[deterministic_scoring] Error on candidate %s — %s",
                cand.get("candidate_name"), exc,
            )
            scored.append({
                **cand,
                "total_experience_months":  0,
                "fulltime_years":           0.0,
                "internship_months":        0,
                "ai_background_detected":   False,
                "matched_skills":           [],
                "stuffing_penalty":         0.0,
                "applied_fixes":            ["[ERROR] Deterministic scoring failed — zeros applied"],
                "implicit_skills":          [],
                "project_complexity_score": 0.0,
                "complexity_dimensions":    [],
                "det_scores": {
                    "technical_match":    0.0,
                    "experience_quality": 0.0,
                    "education_fit":      0.0,
                    "soft_skills":        0.0,
                    "training_score":     0.0,
                    "nice_to_have":       0.0,
                    "project_complexity": 0.0,
                },
            })

    logger.info("[deterministic_scoring] Scored %d candidates.", len(scored))
    return {**state, "scored_candidates": scored}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — LLM Qualitative Assessment
# ─────────────────────────────────────────────────────────────────────────────

def _build_llm_prompt(cand: dict, jd: dict) -> list[dict]:
    """Construct the GPT-4o-mini prompt for a single candidate qualitative assessment."""
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

    candidate_skills_text = (
        ", ".join(s["name"] for s in cand.get("skills", []))[:500]
        or "None listed"
    )

    # Required skills from the JD — explicit list for direct comparison
    required_skills_text = (
        ", ".join(s["name"] for s in jd.get("required_skills", [])[:25])
        or "Not specified"
    )

    # Professional summary (populated by cv_service at upload time)
    professional_summary = (cand.get("professional_summary") or "").strip()[:500] or "Not provided."

    # Responsibility text — prefer key_responsibilities, fall back to full description excerpt
    responsibilities_text = (
        (jd.get("key_responsibilities") or jd.get("full_description") or "")[:500]
    ) or "Not specified."

    system_msg = (
        "You are a senior technical recruiter evaluating candidates for AI/ML and software roles. "
        "Base your evaluation STRICTLY on the data provided. "
        "Never invent experience or skills not mentioned. "
        "ALWAYS respond with valid JSON only — no markdown, no prose."
    )

    user_msg = (
        f"## Job Position\n"
        f"Title: {jd.get('job_title')}\n"
        f"Required Experience: {jd.get('required_years')} years | "
        f"Seniority: {jd.get('seniority_level', 'Not specified')}\n"
        f"Required Skills: {required_skills_text}\n"
        f"Responsibilities: {responsibilities_text}\n\n"
        f"## Candidate: {cand.get('candidate_name')}\n"
        f"Professional Summary: {professional_summary}\n"
        f"Total Experience: {total_yrs}y {total_mo}m (internship: {intern_mo}mo)\n"
        f"Education: {edu_text}\n"
        f"Candidate Skills: {candidate_skills_text}\n\n"
        f"## Projects\n"
        f"{proj_text}\n\n"
        f"## Work Experience\n"
        f"{exp_text}\n\n"
        f"## Scoring Instructions\n"
        f"- project_depth_score: 0-100. Score based on EVIDENCE in the Projects and Work Experience "
        f"sections above. If no projects are listed use 0. If projects show real deployment, "
        f"complex architectures, or measurable impact score 60-90+.\n"
        f"- Assess how well the candidate's skills ({candidate_skills_text}) cover "
        f"the required skills ({required_skills_text}).\n\n"
        f"Respond with VALID JSON (no markdown fences):\n"
        f"{{\n"
        f'  "project_depth_score": <integer 0-100>,\n'
        f'  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],\n'
        f'  "concerns": ["<concern 1>", "<concern 2>"],\n'
        f'  "interview_questions": ["<question 1>", "<question 2>", "<question 3>"],\n'
        f'  "summary": "<2-3 sentence overall assessment based strictly on the data above>"\n'
        f"}}"
    )

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
            # Project complexity boost: max +10 pts (score 1.0 → +10, 0.5 → +5…)
            complexity_boost = round(cand.get("project_complexity_score", 0.0) * 10.0, 2)
            final_score = round(min(100.0, weighted_sum + genai_bonus + complexity_boost), 2)

            # ── Experience ratio floor (Gold Standard) ────────────────────────
            # A candidate with strong skills but fewer than 50% of required years
            # is capped at "Maybe" regardless of score — prevents over-promotion.
            fulltime_yrs = cand.get("fulltime_years", 0.0)
            exp_ratio = min(fulltime_yrs / required_years, 3.0) if required_years > 0 else 1.0
            exp_floor_applied = False
            if required_years > 0 and exp_ratio < 0.5 and final_score >= 55:
                final_score = min(final_score, 54.9)  # cap below "Hire" threshold
                exp_floor_applied = True

            # ── Shapley-style feature attribution (pre-compute for reporting) ──
            intermediate_cand = {
                **cand,
                "component_scores":         component_scores,
                "weight_breakdown":         weights,
                "complexity_boost_applied": complexity_boost,
            }
            feature_attribution = _compute_feature_attribution(intermediate_cand)

            scored.append({
                **cand,
                "final_score":              round(final_score, 2),
                "weighted_sum":             round(weighted_sum, 2),
                "genai_bonus_applied":      genai_bonus,
                "complexity_boost_applied": complexity_boost,
                "exp_ratio":                round(exp_ratio, 3),
                "exp_floor_applied":        exp_floor_applied,
                "scoring_mode":             mode,
                "weight_breakdown":         weights,
                "component_scores":         component_scores,
                "feature_attribution":      feature_attribution,
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
                "final_score":              0.0,
                "weighted_sum":             0.0,
                "genai_bonus_applied":      0.0,
                "complexity_boost_applied": 0.0,
                "exp_ratio":                0.0,
                "exp_floor_applied":        False,
                "scoring_mode":             mode,
                "weight_breakdown":         weights,
                "component_scores":         {},
                "feature_attribution":      [],
                "recommendation":           "Error — Manual Review Required",
            })

    # ── Phase 2: optional CE reranking + hybrid score ────────────────────────
    # If embedding_service is available, run the cross-encoder over all
    # candidates and blend 70% CE probability with 30% skill_overlap.
    # This prevents semantic drift (fluent writing without skills) from
    # outranking skill-rich but terse CVs.
    # Falls back to deterministic final_score if CE model unavailable.
    if _EMBEDDING_SERVICE_AVAILABLE and scored:
        jd_full_text = (
            jd.get("full_description")
            or jd.get("key_responsibilities")
            or jd.get("job_title")
            or ""
        )
        cv_texts  = [c.get("raw_text") or "" for c in scored]
        try:
            ce_logits = _ce_rerank(jd_full_text, cv_texts)
            for cand, logit in zip(scored, ce_logits):
                overlap = cand.get("skill_overlap", 0.0)
                # skill_overlap may not be in state yet — derive from tech score
                if overlap == 0.0:
                    tech = cand.get("det_scores", {}).get("technical_match", 0.0)
                    overlap = round(tech / 100.0, 4)
                hs = _hybrid_score(logit, overlap)
                # Blend: keep final_score as base, re-weight with hybrid signal
                # (0.5 deterministic + 0.5 hybrid keeps rule-based domain knowledge)
                blended = round(0.50 * cand["final_score"] + 0.50 * hs * 100.0, 2)
                cand["ce_logit"]     = round(logit, 4)
                cand["ce_prob"]      = round(_ce_sigmoid(logit), 4)
                cand["hybrid_score"] = hs
                cand["final_score"]  = round(min(100.0, max(0.0, blended)), 2)
            logger.info(
                "[final_ranker] CE reranking applied to %d candidates "
                "(hybrid blend: 50%% deterministic + 50%% CE×skill_overlap).",
                len(scored),
            )
        except Exception as exc:
            logger.warning(
                "[final_ranker] CE reranking failed (%s) — "
                "using deterministic scores only.",
                exc,
            )
    else:
        # Mark CE as not run so downstream (XAI) knows to skip SHAP
        for cand in scored:
            cand.setdefault("ce_logit",     0.0)
            cand.setdefault("ce_prob",      0.0)
            cand.setdefault("hybrid_score", 0.0)

    # ── Phase 3: sort pool → assign rank + pool-relative label ───────────────
    # This is the comparative step — all candidates must be scored first.
    scored.sort(key=lambda c: c["final_score"], reverse=True)
    pool_size = len(scored)
    top_score = scored[0]["final_score"] if scored else 0.0

    for rank, cand in enumerate(scored, start=1):
        cand["rank_in_pool"]       = rank
        cand["total_in_pool"]      = pool_size
        cand["top_score"]          = top_score
        cand["score_gap_to_top"]   = round(top_score - cand["final_score"], 2)
        label = _pool_relative_label(
            rank=rank,
            score=cand["final_score"],
            top_score=top_score,
            pool_size=pool_size,
        )
        # Honour experience floor: never label under-experienced candidate above "Maybe"
        if cand.get("exp_floor_applied") and label in ("Strong Hire", "Hire", "Strong Runner-Up"):
            label = "Maybe"
            cand.setdefault("applied_fixes", []).append(
                "[EXP-FLOOR] Under-experienced vs JD requirements — label capped at Maybe"
            )
        cand["recommendation"] = label
        cand["absolute_tier"]  = _absolute_tier(cand["final_score"])

    # ── Phase 4: XAI explanation cards (optional) ───────────────────────────
    # Compute SHAP word attributions ONLY for top-K (SHAP_K) candidates.
    # xai_service uses AutoTokenizer-based SHAP masker to match WordPiece tokenization.
    if _XAI_SERVICE_AVAILABLE and scored:
        try:
            explainer, _tokenizer = _build_shap_explainer()
            shap_k = min(int(_XAI_SHAP_K), SHAP_K)
            scored = _generate_explanations_for_pool(
                ranked_candidates=scored,
                jd=jd,
                explainer=explainer,
                shap_k=shap_k,
            )
            logger.info(
                "[final_ranker] XAI HR explanations generated (shap_k=%d).",
                shap_k,
            )
        except Exception as exc:
            logger.warning(
                "[final_ranker] XAI explanation generation failed (%s) — continuing without HR cards.",
                exc,
            )

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
    narrative: mode, score breakdown, GenAI evidence, implicit skill evidence,
    project complexity, and LLM technical depth notes.
    Stored in semantic_analysis_reports.ai_insights.
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
    implicit_skills = cand.get("implicit_skills", [])
    complexity_score = cand.get("project_complexity_score", 0.0)
    complexity_dims = cand.get("complexity_dimensions", [])
    complexity_boost = cand.get("complexity_boost_applied", 0.0)

    def w(key: str) -> str:
        return f"{weights.get(key, 0) * 100:.0f}%"

    cs = cand.get("component_scores", {})
    proj_depth = cs.get("project_depth", llm.get("project_depth", 0))

    breakdown = (
        f"  • Technical Match    ({w('technical_match')} weight): {cs.get('technical_match', det.get('technical_match', 0)):.0f}/100\n"
        f"  • Project Depth      ({w('project_depth')} weight): {proj_depth:.0f}/100\n"
        f"  • Education Fit      ({w('education_fit')} weight): {cs.get('education_fit', det.get('education_fit', 0)):.0f}/100\n"
        f"  • Experience Quality ({w('experience_quality')} weight): {cs.get('experience_quality', det.get('experience_quality', 0)):.0f}/100\n"
        f"  • Soft Skills        ({w('soft_skills')} weight): {cs.get('soft_skills', det.get('soft_skills', 0)):.0f}/100\n"
        f"  • Training Score     ({w('training_score')} weight): {cs.get('training_score', det.get('training_score', 0)):.0f}/100\n"
        f"  • Nice-to-Have       ({w('nice_to_have')} weight): {cs.get('nice_to_have', det.get('nice_to_have', 0)):.0f}/100\n"
        f"  • Project Complexity (bonus signal): {det.get('project_complexity', 0):.0f}/100 "
        f"→ +{complexity_boost:.1f}pt boost\n"
        f"  • GenAI Bonus: +{genai.get('bonus', 0):.0f}pt "
        f"(context: {genai.get('context', 'none')}, "
        f"terms: {', '.join(genai.get('evidence', [])) or 'none found'})"
    )

    # Implicit skill evidence section
    if implicit_skills:
        impl_lines = "\n".join(
            f"  • [{skill}] — evidenced: \"{snippet}\""
            for skill, snippet in implicit_skills
        )
        implicit_section = f"\nImplicit Skills (demonstrated in context):\n{impl_lines}"
    else:
        implicit_section = "\nImplicit Skills: none detected"

    # Project complexity breakdown section
    if complexity_dims:
        dims_str = ", ".join(complexity_dims)
        complexity_section = (
            f"\nProject Complexity Analysis:\n"
            f"  Score: {complexity_score:.2f}/1.00 ({len(complexity_dims)}/4 dimensions)\n"
            f"  Dimensions matched: {dims_str}\n"
            f"  Boost applied: +{complexity_boost:.1f} pts"
        )
    else:
        complexity_section = "\nProject Complexity: no complexity signals detected"

    # ── SHAP-style feature attribution (Gold Standard XAI concept) ────────────
    # For a linear weighted-sum model, Shapley value of feature i = weight_i × score_i.
    # Sorted descending by contribution — equivalent to SHAP's token-level output
    # but computed deterministically without GPU or neural model overhead.
    attribution_list = cand.get("feature_attribution", [])
    if attribution_list:
        attr_lines = "\n".join(
            f"  {'▲' if a['direction'] == 'positive' else '▷'} "
            f"{a['feature']:28s} score={a['score']:5.1f}  "
            f"weight={a['weight_pct']:3d}%  contribution=+{a['contribution_pts']:.2f}pts"
            for a in attribution_list
        )
        exp_ratio = cand.get("exp_ratio", 1.0)
        floor_note = " ⚠ EXP-FLOOR APPLIED" if cand.get("exp_floor_applied") else ""
        attribution_section = (
            f"\nFeature Attribution (SHAP-equivalent, linear model):\n"
            f"  Experience ratio: {exp_ratio:.2f}x required{floor_note}\n"
            f"{attr_lines}"
        )
    else:
        attribution_section = "\nFeature Attribution: not computed"

    fixes_text = "\n  ".join(fixes) if fixes else "None"

    # CE reranking signal (from embedding_service hybrid scoring)
    ce_logit = cand.get("ce_logit")
    ce_prob  = cand.get("ce_prob")
    hybrid   = cand.get("hybrid_score")
    if ce_logit is not None and ce_logit != 0.0:
        ce_section = (
            f"\nCE Reranking (bge-reranker-v2-m3):\n"
            f"  Logit: {ce_logit:.4f}  |  Probability: {ce_prob:.4f}  "
            f"|  Hybrid score: {hybrid:.4f}"
        )
    else:
        ce_section = "\nCE Reranking: not applied (model unavailable or CE logit=0)"

    return (
        f"Scoring Mode: {mode}\n"
        f"Pool Position: #{rank} of {total} candidates\n"
        f"Score vs Top Candidate: {cand.get('final_score')} vs {top} "
        f"(gap: {gap:.1f} pts)\n"
        f"\nScore Breakdown:\n{breakdown}\n"
        f"{implicit_section}\n"
        f"{complexity_section}\n"
        f"{attribution_section}\n"
        f"{ce_section}\n"
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
        f"GenAI bonus: +{cand.get('genai_bonus_applied', 0):.0f}, "
        f"complexity boost: +{cand.get('complexity_boost_applied', 0):.1f})\n"
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

    # Remove stale artifacts before writing new ones (idempotency)
    try:
        _cleanup_stale_artifacts(jr_id=int(state.get("requisition_id") or 0))
    except Exception as exc:
        logger.warning("[persistence] Artifact cleanup failed: %s", exc)

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
                    existing.hr_explanation_text = cand.get("hr_explanation")
                    hr_json = cand.get("hr_explanation_json")
                    existing.hr_explanation_json = (
                        json.dumps(hr_json, ensure_ascii=False)
                        if isinstance(hr_json, (dict, list))
                        else (str(hr_json) if hr_json else None)
                    )
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
                        hr_explanation_text=cand.get("hr_explanation"),
                        hr_explanation_json=(
                            json.dumps(cand.get("hr_explanation_json"), ensure_ascii=False)
                            if isinstance(cand.get("hr_explanation_json"), (dict, list))
                            else (str(cand.get("hr_explanation_json")) if cand.get("hr_explanation_json") else None)
                        ),
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

                # Write matched skills (exact / semantic / text evidence)
                matched_skill_names: set[str] = set()
                for matched in cand.get("matched_skills", []):
                    skill_name = matched["skill"]
                    db.add(SemanticMatchedSkill(
                        report_id=report.report_id,
                        skill_name=skill_name,
                        match_type=matched.get("match_type", "exact"),
                    ))
                    matched_skill_names.add(skill_name.lower())

                # Write MISSING skills (JD required skills absent from CV).
                # These appear as match_type='missing' in semantic_matched_skills
                # and are surfaced in GET /api/v1/requisitions/{jr_id}/semantic-analysis
                # as the "missing_skills" list for each candidate.
                jd_required = state.get("jd_data", {}).get("required_skills", [])
                for jd_skill in jd_required:
                    skill_name = jd_skill.get("name") or ""
                    if not skill_name:
                        continue
                    if skill_name.lower() not in matched_skill_names:
                        db.add(SemanticMatchedSkill(
                            report_id=report.report_id,
                            skill_name=skill_name,
                            match_type="missing",
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

        # Write results.json artifact + persist its path on job_requisitions
        try:
            jr_id = int(state.get("requisition_id") or 0)
            json_path = _write_results_json(jr_id=jr_id, jd=state.get("jd_data") or {}, ranked=ranked)

            jr = (
                db.query(JobRequisition)
                .filter(JobRequisition.requisition_id == jr_id)
                .first()
            )
            if jr:
                jr.results_json_path = json_path
                db.commit()
                logger.info(
                    "[persistence] results_json_path updated for requisition %d: %s",
                    jr_id, json_path,
                )
        except Exception as exc:
            db.rollback()
            logger.warning("[persistence] Could not write/save results.json path: %s", exc)

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


# ─────────────────────────────────────────────────────────────────────────────
# Node 7 — Technical Interview (Optional for top candidates)
# ─────────────────────────────────────────────────────────────────────────────

def technical_interview_node(state: RankingState) -> RankingState:
    """
    Conduct technical interviews for top-ranked candidates using the
    Technical_Interview_Agent. Only triggers for candidates with
    recommendation "Strongly Recommend Interview" or "Consider for Interview".
    """
    # Lazy import — heavy ML libraries (whisper, torch, etc.) must not load at startup
    try:
        from .technical_interview_agent import interview_agent, InterviewMode  # noqa: PLC0415
    except Exception as _import_exc:
        logger.warning("[technical_interview] Agent unavailable — skipping: %s", _import_exc)
        return state

    ranked_candidates = state.get("ranked_candidates", [])
    if not ranked_candidates:
        logger.info("[technical_interview] No candidates to interview")
        return state
    
    # Filter candidates eligible for interview
    interview_eligible = [
        cand for cand in ranked_candidates
        if cand.get("recommendation") in ["Strongly Recommend Interview", "Consider for Interview"]
    ]
    
    if not interview_eligible:
        logger.info("[technical_interview] No candidates eligible for interview")
        return state
    
    logger.info(f"[technical_interview] Starting interviews for {len(interview_eligible)} candidates")
    
    # Conduct interviews asynchronously
    interview_results = []
    for cand in interview_eligible:
        try:
            application_id = cand.get("application_id")
            if not application_id:
                continue
            
            # Determine interview mode based on job requirements
            jd_data = state.get("jd_data", {})
            mode = InterviewMode.TECHNICAL  # Default to technical mode
            
            # Conduct interview with Focused Interviewer Persona (5-round structure)
            session = asyncio.run(interview_agent.conduct_interview(application_id, mode, use_persona="focused"))
            
            # Generate scorecard
            scorecard = interview_agent.generate_candidate_scorecard(session)
            
            # Update candidate data with interview results
            cand["interview_session_id"] = session.session_id
            cand["interview_score"] = session.final_score
            cand["combined_score"] = scorecard["scores"]["combined_score"]
            cand["interview_completed_at"] = session.completed_at.isoformat() if session.completed_at else None
            cand["final_recommendation"] = scorecard["recommendation"]
            
            interview_results.append({
                "application_id": application_id,
                "session_id": session.session_id,
                "scorecard": scorecard
            })
            
            logger.info(
                f"[technical_interview] Interview completed for candidate {cand.get('candidate_name')}. "
                f"Score: {session.final_score:.2f}, Combined: {scorecard['scores']['combined_score']:.2f}"
            )
            
        except Exception as exc:
            logger.error(
                f"[technical_interview] Interview failed for candidate {cand.get('candidate_name')}: {exc}"
            )
            # Mark interview as failed but keep candidate in pipeline
            cand["interview_failed"] = True
            cand["interview_error"] = str(exc)
    
    # Sort candidates by combined score (screening + interview)
    ranked_candidates.sort(key=lambda c: c.get("combined_score", c.get("final_score", 0)), reverse=True)
    
    logger.info(f"[technical_interview] Completed {len(interview_results)} interviews")
    
    return {
        **state,
        "ranked_candidates": ranked_candidates,
        "interview_results": interview_results
    }


def _build_ranking_graph():
    graph = StateGraph(RankingState)

    graph.add_node("context_gatherer", context_gatherer_node)
    graph.add_node("deterministic_scoring", deterministic_scoring_node)
    graph.add_node("llm_qualitative", llm_qualitative_node)
    graph.add_node("genai_validator", genai_validator_node)
    graph.add_node("final_ranker", final_ranker_node)
    graph.add_node("persistence", persistence_node)
    graph.add_node("technical_interview", technical_interview_node)

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
    graph.add_edge("persistence", "technical_interview")
    graph.add_edge("technical_interview", END)

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

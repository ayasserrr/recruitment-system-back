"""
Ensemble Scorer — Phase 6 (Assessment) Multi-Model Grading Engine
══════════════════════════════════════════════════════════════════

Scores open-ended assessment answers using 5 complementary models:

  1. DeBERTa NLI   (cross-encoder/nli-deberta-v3-small) — fact entailment check
  2. BGE bi-encoder (reused from embedding_service)       — semantic similarity
  3. MPNet          (all-mpnet-base-v2)                   — second semantic signal
  4. RoBERTa QA    (deepset/roberta-base-squad2)          — extractive QA confidence
  5. TF-IDF        (sklearn)                              — keyword overlap baseline

SHAP is computed analytically (exact for linear models) — no KernelExplainer
overhead.  The composite ensemble score replaces the LLM-only score for
open-ended answers; MCQ answers bypass this module entirely.

Usage (called from grading_graph.ensemble_score_node):
    from services.ensemble_scorer import score_open_answer, FEATURE_ORDER

    result = score_open_answer(
        question_text   = q["question_text"],
        ideal_answer    = q["correct_answer"],
        required_keywords = q["required_keywords"],
        candidate_answer  = candidate_answer,
        question_points   = q["points"],
    )
    # result keys: nli_score, semantic_bge_score, semantic_mpnet_score,
    #              roberta_qa_score, tfidf_score, ensemble_score_01,
    #              ensemble_points, shap_json
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from core.gpu import DEVICE  # sets HF_HOME env vars as a side-effect

logger = logging.getLogger(__name__)

# ── Model identifiers ─────────────────────────────────────────────────────────
_MPNET_MODEL_ID  = "sentence-transformers/all-mpnet-base-v2"
_NLI_MODEL_ID    = "cross-encoder/nli-deberta-v3-small"
_ROBERTA_QA_ID   = "deepset/roberta-base-squad2"
_HF_CACHE        = "E:/huggingface_cache"

# ── Lazy model singletons ─────────────────────────────────────────────────────
_mpnet:    "SentenceTransformer | None" = None  # type: ignore[name-defined]
_nli_pipe: "Pipeline | None"            = None  # type: ignore[name-defined]
_qa_pipe:  "Pipeline | None"            = None  # type: ignore[name-defined]

# ── Ensemble weights (must sum to 1.0) ────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "nli_score":            0.30,
    "semantic_bge_score":   0.25,
    "semantic_mpnet_score": 0.20,
    "roberta_qa_score":     0.15,
    "tfidf_score":          0.10,
}

# Expected score for a mediocre (neutral) answer — used as SHAP baseline
_BASELINES: dict[str, float] = {
    "nli_score":            0.50,
    "semantic_bge_score":   0.30,
    "semantic_mpnet_score": 0.30,
    "roberta_qa_score":     0.20,
    "tfidf_score":          0.20,
}

FEATURE_ORDER: list[str] = list(WEIGHTS.keys())


# ── Lazy loaders ──────────────────────────────────────────────────────────────

def _get_mpnet():
    global _mpnet
    if _mpnet is not None:
        return _mpnet
    try:
        from sentence_transformers import SentenceTransformer
        _mpnet = SentenceTransformer(_MPNET_MODEL_ID, device=DEVICE, cache_folder=_HF_CACHE)
        logger.info("[ensemble] MPNet loaded on %s.", DEVICE)
    except Exception as exc:
        logger.warning("[ensemble] MPNet unavailable: %s", exc)
    return _mpnet


def _get_nli():
    global _nli_pipe
    if _nli_pipe is not None:
        return _nli_pipe
    try:
        from transformers import pipeline
        device_id = 0 if DEVICE == "cuda" else -1
        _nli_pipe = pipeline(
            "zero-shot-classification",
            model=_NLI_MODEL_ID,
            device=device_id,
            model_kwargs={"cache_dir": _HF_CACHE},
        )
        logger.info("[ensemble] DeBERTa NLI loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[ensemble] DeBERTa NLI unavailable: %s", exc)
    return _nli_pipe


def _get_qa():
    global _qa_pipe
    if _qa_pipe is not None:
        return _qa_pipe
    try:
        from transformers import pipeline
        device_id = 0 if DEVICE == "cuda" else -1
        _qa_pipe = pipeline(
            "question-answering",
            model=_ROBERTA_QA_ID,
            device=device_id,
            model_kwargs={"cache_dir": _HF_CACHE},
        )
        logger.info("[ensemble] RoBERTa QA loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[ensemble] RoBERTa QA unavailable: %s", exc)
    return _qa_pipe


# ── Individual model scorers ──────────────────────────────────────────────────

def _score_nli(ideal: str, candidate: str) -> float:
    """
    DeBERTa NLI: probability that the candidate's answer demonstrates
    understanding of the required concept(s).

    Uses zero-shot-classification with binary labels rather than raw NLI
    (premise, hypothesis) pairs, which is the documented and stable API
    for cross-encoder NLI models via the Transformers pipeline.
    """
    pipe = _get_nli()
    if pipe is None or not candidate.strip():
        return _BASELINES["nli_score"]
    try:
        # Use the ideal answer as part of the hypothesis template so the model
        # checks alignment against the *specific* concept, not just generically.
        concept_hint = (ideal[:80].strip() + "...") if len(ideal) > 80 else ideal.strip()
        result = pipe(
            candidate[:512],
            candidate_labels=["demonstrates correct understanding", "does not understand"],
            hypothesis_template="This response {} of: " + concept_hint,
        )
        # scores[0] = P("demonstrates correct understanding")
        return round(float(result["scores"][0]), 4)
    except Exception as exc:
        logger.warning("[ensemble] NLI score failed: %s", exc)
        return _BASELINES["nli_score"]


def _score_bge(ideal: str, candidate: str) -> float:
    """BGE bi-encoder cosine similarity (reuses embedding_service singletons)."""
    try:
        from services.embedding_service import encode_jd, encode_cv, cosine_similarity
        ideal_vec = encode_jd(ideal)
        cand_vec  = encode_cv(candidate)
        if not ideal_vec or not cand_vec:
            return _BASELINES["semantic_bge_score"]
        return round(max(0.0, min(1.0, cosine_similarity(ideal_vec, cand_vec))), 4)
    except Exception as exc:
        logger.warning("[ensemble] BGE score failed: %s", exc)
        return _BASELINES["semantic_bge_score"]


def _score_mpnet(ideal: str, candidate: str) -> float:
    """MPNet symmetric cosine similarity."""
    model = _get_mpnet()
    if model is None:
        return _BASELINES["semantic_mpnet_score"]
    try:
        from sentence_transformers import util
        embs  = model.encode([ideal, candidate], normalize_embeddings=True, convert_to_tensor=True)
        score = float(util.cos_sim(embs[0], embs[1]))
        return round(max(0.0, min(1.0, score)), 4)
    except Exception as exc:
        logger.warning("[ensemble] MPNet score failed: %s", exc)
        return _BASELINES["semantic_mpnet_score"]


def _score_roberta_qa(question: str, ideal: str, candidate: str) -> float:
    """
    RoBERTa QA: extract an answer span from the candidate's response, then
    measure keyword overlap with the ideal answer.  Blends extraction
    confidence (0-1) with token overlap to penalise fluent-but-wrong answers.
    """
    pipe = _get_qa()
    if pipe is None or not candidate.strip():
        return _BASELINES["roberta_qa_score"]
    try:
        result     = pipe(question=question[:200], context=candidate[:1000])
        extracted  = result.get("answer", "").lower().strip()
        confidence = min(1.0, float(result.get("score", 0.0)))

        ideal_tokens     = set(ideal.lower().split())
        extracted_tokens = set(extracted.split())
        overlap = len(ideal_tokens & extracted_tokens) / max(len(ideal_tokens), 1)

        # 50% confidence weight + 50% keyword overlap weight
        return round(min(1.0, 0.5 * confidence + 0.5 * overlap), 4)
    except Exception as exc:
        logger.warning("[ensemble] RoBERTa QA score failed: %s", exc)
        return _BASELINES["roberta_qa_score"]


def _score_tfidf(ideal: str, candidate: str) -> float:
    """TF-IDF bigram cosine similarity (deterministic, no model needed)."""
    if not ideal.strip() or not candidate.strip():
        return _BASELINES["tfidf_score"]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as sk_cosine
        vec    = TfidfVectorizer(ngram_range=(1, 2), max_features=500)
        matrix = vec.fit_transform([ideal, candidate])
        score  = float(sk_cosine(matrix[0], matrix[1])[0][0])
        return round(max(0.0, min(1.0, score)), 4)
    except Exception as exc:
        logger.warning("[ensemble] TF-IDF score failed: %s", exc)
        return _BASELINES["tfidf_score"]


# ── SHAP (analytical for linear model) ───────────────────────────────────────

def compute_shap(feature_scores: dict[str, float]) -> dict[str, float]:
    """
    Exact SHAP values for the linear weighted ensemble.

    For f(x) = sum(w_i * x_i), the Shapley value for feature i is:
        phi_i = w_i * (x_i - E[x_i])
    where E[x_i] is the baseline (expected score for a neutral answer).

    Positive phi_i → this model's signal pushed the score ABOVE baseline.
    Negative phi_i → this model's signal pulled the score BELOW baseline.
    """
    return {
        feat: round(WEIGHTS[feat] * (feature_scores.get(feat, _BASELINES[feat]) - _BASELINES[feat]), 4)
        for feat in FEATURE_ORDER
    }


def shap_summary_text(shap_vals: dict[str, float]) -> str:
    """Human-readable one-liner explaining which models drove the score."""
    sorted_feats = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    pos = [(f, v) for f, v in sorted_feats if v > 0.001][:2]
    neg = [(f, v) for f, v in sorted_feats if v < -0.001][:1]
    parts = [f"+{v:.3f} ({f})" for f, v in pos] + [f"{v:.3f} ({f})" for f, v in neg]
    return "Score drivers: " + ", ".join(parts) if parts else "Score near baseline across all models."


# ── Main entry point ──────────────────────────────────────────────────────────

def score_open_answer(
    question_text:     str,
    ideal_answer:      str,
    required_keywords: list[str],
    candidate_answer:  str,
    question_points:   int | float,
) -> dict:
    """
    Run all 5 models on a single open-ended answer.

    Returns a dict with:
        nli_score, semantic_bge_score, semantic_mpnet_score,
        roberta_qa_score, tfidf_score,
        ensemble_score_01  — composite score in [0, 1]
        ensemble_points    — scaled to question_points
        shap_json          — JSON string of per-model SHAP values
        shap_summary       — human-readable explanation
    """
    if not candidate_answer.strip():
        zero_feats = {f: 0.0 for f in FEATURE_ORDER}
        shap_vals  = compute_shap(zero_feats)
        return {
            **zero_feats,
            "ensemble_score_01": 0.0,
            "ensemble_points":   0.0,
            "shap_json":         json.dumps(shap_vals),
            "shap_summary":      "Empty answer — all model scores zero.",
        }

    # Build ideal reference: prefer keywords (more precise), fall back to model answer
    keywords_str = "; ".join(required_keywords[:8]) if required_keywords else ""
    reference    = keywords_str if keywords_str else ideal_answer

    feature_scores: dict[str, float] = {
        "nli_score":            _score_nli(reference, candidate_answer),
        "semantic_bge_score":   _score_bge(reference, candidate_answer),
        "semantic_mpnet_score": _score_mpnet(reference, candidate_answer),
        "roberta_qa_score":     _score_roberta_qa(question_text, reference, candidate_answer),
        "tfidf_score":          _score_tfidf(reference, candidate_answer),
    }

    ensemble_01 = round(
        sum(WEIGHTS[f] * feature_scores[f] for f in FEATURE_ORDER), 4
    )
    ensemble_pts = round(ensemble_01 * float(question_points), 2)

    shap_vals = compute_shap(feature_scores)
    summary   = shap_summary_text(shap_vals)

    logger.debug(
        "[ensemble] Q='%s...' → NLI=%.3f BGE=%.3f MPNet=%.3f QA=%.3f TF-IDF=%.3f → %.3f/%.0f",
        question_text[:40], feature_scores["nli_score"], feature_scores["semantic_bge_score"],
        feature_scores["semantic_mpnet_score"], feature_scores["roberta_qa_score"],
        feature_scores["tfidf_score"], ensemble_pts, question_points,
    )

    return {
        **feature_scores,
        "ensemble_score_01": ensemble_01,
        "ensemble_points":   ensemble_pts,
        "shap_json":         json.dumps(shap_vals),
        "shap_summary":      summary,
    }

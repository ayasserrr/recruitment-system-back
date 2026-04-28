"""
Technical Interview Analysis Service — Deep Technical Depth Scoring
════════════════════════════════════════════════════════════════════

Analyzes candidate responses during technical (live) interviews using 4 models:

  1. CodeBERT   (microsoft/codebert-base)               — technical semantic depth
  2. RoBERTa-QA (deepset/roberta-base-squad2)           — extractive QA correctness
  3. DeBERTa NLI (cross-encoder/nli-deberta-v3-small)   — technical concept alignment
  4. TF-IDF Bigram (sklearn)                             — technical keyword coverage

Feature vector:
    [codebert_score, roberta_depth_score, nli_technical_score, tfidf_technical_score]

Weights:
    codebert_score:        0.40
    roberta_depth_score:   0.30
    nli_technical_score:   0.20
    tfidf_technical_score: 0.10

Designed to be called from tech_interview_node after the live interview ends
and a transcript is available.

All models are loaded lazily and served from E:/huggingface_cache.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from core.gpu import DEVICE  # sets HF_HOME env vars as side-effect

logger = logging.getLogger(__name__)

# ── Model identifiers ─────────────────────────────────────────────────────────
_CODEBERT_MODEL_ID = "microsoft/codebert-base"
_ROBERTA_QA_ID     = "deepset/roberta-base-squad2"
_NLI_MODEL_ID      = "cross-encoder/nli-deberta-v3-small"
_HF_CACHE          = "E:/huggingface_cache"

# ── Lazy singletons ───────────────────────────────────────────────────────────
_codebert_pipe: "Pipeline | None" = None  # type: ignore[name-defined]
_qa_pipe:       "Pipeline | None" = None  # type: ignore[name-defined]
_nli_pipe:      "Pipeline | None" = None  # type: ignore[name-defined]

# ── Scoring weights (must sum to 1.0) ─────────────────────────────────────────
TECH_WEIGHTS: dict[str, float] = {
    "codebert_score":        0.40,
    "roberta_depth_score":   0.30,
    "nli_technical_score":   0.20,
    "tfidf_technical_score": 0.10,
}

# Expected score for a mediocre technical response — SHAP reference point
_TECH_BASELINES: dict[str, float] = {
    "codebert_score":        0.35,
    "roberta_depth_score":   0.25,
    "nli_technical_score":   0.40,
    "tfidf_technical_score": 0.20,
}

TECH_FEATURE_ORDER: list[str] = list(TECH_WEIGHTS.keys())


# ── Lazy loaders ──────────────────────────────────────────────────────────────

def _get_codebert():
    """Load CodeBERT as a feature-extraction pipeline (mean-pooled embeddings)."""
    global _codebert_pipe
    if _codebert_pipe is not None:
        return _codebert_pipe
    try:
        from transformers import pipeline
        device_id = 0 if DEVICE == "cuda" else -1
        _codebert_pipe = pipeline(
            "feature-extraction",
            model=_CODEBERT_MODEL_ID,
            device=device_id,
            model_kwargs={"cache_dir": _HF_CACHE},
        )
        logger.info("[tech_analysis] CodeBERT loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[tech_analysis] CodeBERT unavailable: %s", exc)
    return _codebert_pipe


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
        logger.info("[tech_analysis] RoBERTa-QA loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[tech_analysis] RoBERTa-QA unavailable: %s", exc)
    return _qa_pipe


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
        logger.info("[tech_analysis] DeBERTa NLI loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[tech_analysis] DeBERTa NLI unavailable: %s", exc)
    return _nli_pipe


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mean_pool(pipeline_output: list) -> list[float]:
    """
    Mean-pool a feature-extraction pipeline output to a single vector.
    Output shape from pipeline: [batch=1][seq_len][hidden_size]
    We take batch[0] and average across seq_len.
    """
    tokens = pipeline_output[0]       # (seq_len, hidden_size)
    n      = len(tokens)
    hidden = len(tokens[0])
    return [sum(tokens[i][j] for i in range(n)) / n for j in range(hidden)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Individual model scorers ──────────────────────────────────────────────────

def _score_codebert(candidate_text: str, technical_context: str) -> float:
    """
    CodeBERT cosine similarity between the candidate's technical response
    and the job's technical context (title + responsibilities).

    CodeBERT was pre-trained on code + natural language pairs, making it
    sensitive to technical vocabulary and reasoning patterns that general
    BERT/MPNet models under-weight.
    """
    pipe = _get_codebert()
    if pipe is None or not candidate_text.strip():
        return _TECH_BASELINES["codebert_score"]
    try:
        cand_emb = pipe(candidate_text[:512])
        tech_emb = pipe(technical_context[:256])
        cand_vec = _mean_pool(cand_emb)
        tech_vec = _mean_pool(tech_emb)
        return round(max(0.0, min(1.0, _cosine(cand_vec, tech_vec))), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] CodeBERT score failed: %s", exc)
        return _TECH_BASELINES["codebert_score"]


def _score_roberta_depth(question: str, candidate_text: str, reference: str) -> float:
    """
    RoBERTa-QA: extract a technical answer span from the candidate's response,
    then blend extraction confidence with keyword overlap against the reference.
    Penalises fluent-but-vague answers that don't contain the key technical terms.
    """
    pipe = _get_qa()
    if pipe is None or not candidate_text.strip():
        return _TECH_BASELINES["roberta_depth_score"]
    try:
        result     = pipe(question=question[:200], context=candidate_text[:1000])
        extracted  = result.get("answer", "").lower().strip()
        confidence = min(1.0, float(result.get("score", 0.0)))

        ref_tokens  = set(reference.lower().split())
        extr_tokens = set(extracted.split())
        overlap = len(ref_tokens & extr_tokens) / max(len(ref_tokens), 1)

        return round(min(1.0, 0.5 * confidence + 0.5 * overlap), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] RoBERTa-QA depth score failed: %s", exc)
        return _TECH_BASELINES["roberta_depth_score"]


def _score_nli_technical(candidate_text: str, technical_context: str) -> float:
    """
    DeBERTa NLI: probability that the candidate's response is technically
    sound and directly relevant to the stated job requirements.
    """
    pipe = _get_nli()
    if pipe is None or not candidate_text.strip():
        return _TECH_BASELINES["nli_technical_score"]
    try:
        context_hint = (
            (technical_context[:100].strip() + "...")
            if len(technical_context) > 100 else technical_context
        )
        result = pipe(
            candidate_text[:512],
            candidate_labels=[
                "technically sound and relevant to the role",
                "lacks technical depth or relevance",
            ],
            hypothesis_template="This response {} of: " + context_hint,
        )
        return round(float(result["scores"][0]), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] NLI technical score failed: %s", exc)
        return _TECH_BASELINES["nli_technical_score"]


def _score_tfidf_technical(candidate_text: str, technical_context: str) -> float:
    """TF-IDF bigram cosine similarity — deterministic technical keyword coverage."""
    if not candidate_text.strip() or not technical_context.strip():
        return _TECH_BASELINES["tfidf_technical_score"]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as sk_cosine
        vec    = TfidfVectorizer(ngram_range=(1, 2), max_features=500)
        matrix = vec.fit_transform([technical_context, candidate_text])
        score  = float(sk_cosine(matrix[0], matrix[1])[0][0])
        return round(max(0.0, min(1.0, score)), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] TF-IDF technical score failed: %s", exc)
        return _TECH_BASELINES["tfidf_technical_score"]


# ── SHAP (analytical for linear model) ───────────────────────────────────────

def compute_tech_shap(feature_scores: dict[str, float]) -> dict[str, float]:
    """
    Exact SHAP values for the linear technical scoring function.
    phi_i = w_i * (x_i - baseline_i)
    """
    return {
        feat: round(
            TECH_WEIGHTS[feat] * (feature_scores.get(feat, _TECH_BASELINES[feat]) - _TECH_BASELINES[feat]),
            4,
        )
        for feat in TECH_FEATURE_ORDER
    }


def tech_shap_summary(shap_vals: dict[str, float], candidate_name: str = "Candidate") -> str:
    """Human-readable SHAP narrative for the technical interview report."""
    sorted_feats = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    pos = [(f, v) for f, v in sorted_feats if v > 0.005][:2]
    neg = [(f, v) for f, v in sorted_feats if v < -0.005][:1]
    lines = [f"SHAP — Technical Interview Score Drivers for {candidate_name}:"]
    lines += [f"  + {f}: +{v:.3f}" for f, v in pos]
    lines += [f"  - {f}: {v:.3f}"  for f, v in neg]
    return "\n".join(lines)


# ── Transcript parser ─────────────────────────────────────────────────────────

def _extract_candidate_turns(transcript: str) -> list[str]:
    """
    Extract candidate-only turns from a technical interview transcript.
    Supports labeled format ("Candidate: ...") and alternating-line format.
    """
    if not transcript.strip():
        return []

    candidate_pattern = re.compile(
        r"(?:candidate|applicant|interviewee)\s*:\s*(.+?)(?=\n(?:interviewer|hr|recruiter|engineer)\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = candidate_pattern.findall(transcript)
    if matches:
        return [m.strip() for m in matches if m.strip()]

    # Alternating-line fallback: assume interviewer goes first (even lines)
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    return [lines[i] for i in range(1, len(lines), 2)]


# ── Main entry point ──────────────────────────────────────────────────────────

def score_tech_transcript(
    transcript:           str,
    job_title:            str,
    job_responsibilities: str,
    candidate_name:       str = "Candidate",
) -> dict:
    """
    Score a full technical interview transcript.

    Extracts candidate turns, applies all 4 models per turn, aggregates means,
    and computes a composite technical depth score with SHAP explanation.

    Returns:
        codebert_score        — mean CodeBERT semantic depth (0-1)
        roberta_depth_score   — mean RoBERTa-QA extraction score (0-1)
        nli_technical_score   — mean NLI technical alignment (0-1)
        tfidf_technical_score — TF-IDF keyword coverage vs JD (0-1)
        composite_score       — weighted ensemble (0-1)
        shap_json             — JSON string of per-feature SHAP values
        shap_summary          — human-readable narrative
        turn_count            — number of candidate turns analyzed
    """
    if not transcript or not transcript.strip():
        zero_feats = {f: 0.0 for f in TECH_FEATURE_ORDER}
        shap_vals  = compute_tech_shap(zero_feats)
        return {
            **zero_feats,
            "composite_score": 0.0,
            "shap_json":       json.dumps(shap_vals),
            "shap_summary":    "No transcript available.",
            "turn_count":      0,
        }

    technical_context = f"{job_title}: {job_responsibilities}"

    turns = _extract_candidate_turns(transcript)
    if not turns:
        turns = [transcript[:2000]]

    codebert_scores:  list[float] = []
    roberta_scores:   list[float] = []
    nli_scores:       list[float] = []

    for turn_text in turns:
        if not turn_text.strip():
            continue
        codebert_scores.append(_score_codebert(turn_text, technical_context))
        roberta_scores.append(
            _score_roberta_depth(job_title, turn_text, job_responsibilities)
        )
        nli_scores.append(_score_nli_technical(turn_text, technical_context))

    def _safe_mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    # TF-IDF: computed once on the full candidate text vs technical context
    combined_text = " ".join(turns[:10])
    tfidf = _score_tfidf_technical(combined_text, technical_context)

    feature_scores: dict[str, float] = {
        "codebert_score":        _safe_mean(codebert_scores),
        "roberta_depth_score":   _safe_mean(roberta_scores),
        "nli_technical_score":   _safe_mean(nli_scores),
        "tfidf_technical_score": tfidf,
    }

    composite = round(
        sum(TECH_WEIGHTS[f] * feature_scores[f] for f in TECH_FEATURE_ORDER), 4
    )

    shap_vals = compute_tech_shap(feature_scores)
    summary   = tech_shap_summary(shap_vals, candidate_name)

    logger.info(
        "[tech_analysis] Transcript scored — turns=%d, codebert=%.3f, "
        "roberta=%.3f, nli=%.3f, tfidf=%.3f → composite=%.3f",
        len(turns),
        feature_scores["codebert_score"],
        feature_scores["roberta_depth_score"],
        feature_scores["nli_technical_score"],
        feature_scores["tfidf_technical_score"],
        composite,
    )

    return {
        **feature_scores,
        "composite_score": composite,
        "shap_json":       json.dumps(shap_vals),
        "shap_summary":    summary,
        "turn_count":      len(turns),
    }

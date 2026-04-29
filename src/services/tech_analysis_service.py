"""
Technical Interview Analysis Service
══════════════════════════════════════════════════════════════════════════════
Scores a technical interview transcript using 4 models instead of relying
on the LLM's self-reported per-answer scores.

Models
──────
1. CodeBERT   (microsoft/codebert-base)                — technical semantic depth
2. DeBERTa NLI (cross-encoder/nli-deberta-v3-small)    — technical alignment with JD
3. TF-IDF     (scikit-learn, no download)              — technical keyword coverage
4. RoBERTa    (cardiffnlp/twitter-roberta-base-sentiment-latest) — response confidence/quality

Weights
───────
nli_technical_score:  0.35
codebert_score:       0.30
tfidf_technical_score: 0.20
roberta_depth_score:  0.15

Composite is 0–1, then scaled to 0–100 to replace overall_score.

SHAP values are computed analytically (exact for linear models).
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Optional

from core.gpu import DEVICE

logger = logging.getLogger(__name__)

_CODEBERT_MODEL_ID  = "microsoft/codebert-base"
_NLI_MODEL_ID       = "cross-encoder/nli-deberta-v3-small"
_SENTIMENT_MODEL_ID = "cardiffnlp/twitter-roberta-base-sentiment-latest"
_HF_CACHE           = "E:/huggingface_cache"

# ── Lazy singletons ───────────────────────────────────────────────────────────
_codebert_pipe:  "Pipeline | None" = None  # type: ignore[name-defined]
_nli_pipe:       "Pipeline | None" = None  # type: ignore[name-defined]
_sentiment_pipe: "Pipeline | None" = None  # type: ignore[name-defined]

# ── Weights & baselines ───────────────────────────────────────────────────────
TECH_WEIGHTS: dict[str, float] = {
    "nli_technical_score":   0.35,
    "codebert_score":        0.30,
    "tfidf_technical_score": 0.20,
    "roberta_depth_score":   0.15,
}

_TECH_BASELINES: dict[str, float] = {
    "nli_technical_score":   0.40,
    "codebert_score":        0.30,
    "tfidf_technical_score": 0.25,
    "roberta_depth_score":   0.50,
}

TECH_FEATURE_ORDER: list[str] = list(TECH_WEIGHTS.keys())

# Common filler words to ignore in TF-IDF extraction
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "i", "we", "you", "he", "she", "they", "it", "my", "our",
    "your", "his", "her", "their", "its", "not", "so", "as", "if", "then",
    "than", "when", "where", "how", "what", "which", "who", "also", "just",
})


# ── Lazy loaders ──────────────────────────────────────────────────────────────

def _get_codebert():
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


def _get_sentiment():
    global _sentiment_pipe
    if _sentiment_pipe is not None:
        return _sentiment_pipe
    try:
        from transformers import pipeline
        device_id = 0 if DEVICE == "cuda" else -1
        _sentiment_pipe = pipeline(
            "text-classification",
            model=_SENTIMENT_MODEL_ID,
            device=device_id,
            model_kwargs={"cache_dir": _HF_CACHE},
        )
        logger.info("[tech_analysis] RoBERTa Sentiment loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[tech_analysis] RoBERTa Sentiment unavailable: %s", exc)
    return _sentiment_pipe


# ── Individual scorers ────────────────────────────────────────────────────────

def _encode_codebert(text: str) -> Optional[list[float]]:
    """Mean-pool CodeBERT token embeddings → dense vector."""
    pipe = _get_codebert()
    if pipe is None or not text.strip():
        return None
    try:
        # shape: [1, seq_len, hidden] → mean over seq_len
        output = pipe(text[:512], return_tensors=False)
        if not output or not output[0]:
            return None
        token_vecs = output[0]  # list of [hidden_size] lists
        n = len(token_vecs)
        hidden = len(token_vecs[0])
        mean_vec = [sum(token_vecs[t][h] for t in range(n)) / n for h in range(hidden)]
        return mean_vec
    except Exception as exc:
        logger.warning("[tech_analysis] CodeBERT encoding failed: %s", exc)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (mag_a * mag_b)))


def _score_codebert(candidate_text: str, jd_text: str) -> float:
    """
    Cosine similarity between CodeBERT embeddings of candidate answer and JD.
    High score = candidate used technically deep language aligned with the role.
    """
    vec_a = _encode_codebert(candidate_text[:512])
    vec_b = _encode_codebert(jd_text[:512])
    if vec_a is None or vec_b is None:
        return _TECH_BASELINES["codebert_score"]
    return round(_cosine(vec_a, vec_b), 4)


def _score_nli_alignment(candidate_text: str, job_context: str) -> float:
    """
    DeBERTa NLI: probability that candidate's response demonstrates
    technical knowledge relevant to the role.
    """
    pipe = _get_nli()
    if pipe is None or not candidate_text.strip() or not job_context.strip():
        return _TECH_BASELINES["nli_technical_score"]
    try:
        context_hint = (job_context[:120].strip() + "...") if len(job_context) > 120 else job_context
        result = pipe(
            candidate_text[:512],
            candidate_labels=[
                "demonstrates relevant technical knowledge",
                "does not demonstrate relevant technical knowledge",
            ],
            hypothesis_template="This response {} for the role of: " + context_hint,
        )
        return round(float(result["scores"][0]), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] NLI alignment failed: %s", exc)
        return _TECH_BASELINES["nli_technical_score"]


def _score_tfidf_coverage(candidate_text: str, jd_text: str) -> float:
    """
    TF-IDF keyword coverage: fraction of significant JD technical terms
    that appear in the candidate's combined answers.
    No model download required — pure sklearn TF-IDF.
    """
    if not candidate_text.strip() or not jd_text.strip():
        return _TECH_BASELINES["tfidf_technical_score"]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        # Extract top-30 technical keywords from JD
        vectorizer = TfidfVectorizer(
            stop_words=list(_STOP_WORDS),
            ngram_range=(1, 2),
            max_features=200,
            sublinear_tf=True,
        )
        vectorizer.fit([jd_text])
        feature_names = vectorizer.get_feature_names_out()
        jd_vec = vectorizer.transform([jd_text]).toarray()[0]
        # Sort by TF-IDF weight, take top 30 keywords
        top_indices = jd_vec.argsort()[-30:][::-1]
        top_keywords = [feature_names[i] for i in top_indices if jd_vec[i] > 0]

        if not top_keywords:
            return _TECH_BASELINES["tfidf_technical_score"]

        cand_lower = candidate_text.lower()
        matched = sum(1 for kw in top_keywords if kw.lower() in cand_lower)
        score = matched / len(top_keywords)
        return round(min(1.0, score), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] TF-IDF coverage failed: %s", exc)
        return _TECH_BASELINES["tfidf_technical_score"]


def _score_response_quality(text: str) -> float:
    """
    RoBERTa Sentiment as a proxy for response confidence/quality.
    Confident, positive delivery in a technical context correlates
    with better command of the subject matter.
    POSITIVE → 1.0, NEUTRAL → 0.5, NEGATIVE → 0.0, weighted by confidence.
    """
    pipe = _get_sentiment()
    if pipe is None or not text.strip():
        return _TECH_BASELINES["roberta_depth_score"]
    try:
        result = pipe(text[:512])
        if not result:
            return _TECH_BASELINES["roberta_depth_score"]
        label = result[0]["label"].upper()
        conf  = float(result[0]["score"])
        mapping = {"POSITIVE": 1.0, "NEUTRAL": 0.5, "NEGATIVE": 0.0,
                   "POS": 1.0, "NEU": 0.5, "NEG": 0.0}
        raw = mapping.get(label, 0.5)
        blended = 0.5 + (raw - 0.5) * conf
        return round(max(0.0, min(1.0, blended)), 4)
    except Exception as exc:
        logger.warning("[tech_analysis] Response quality score failed: %s", exc)
        return _TECH_BASELINES["roberta_depth_score"]


# ── SHAP (analytical for linear model) ───────────────────────────────────────

def compute_tech_shap(feature_scores: dict[str, float]) -> dict[str, float]:
    """phi_i = w_i * (x_i - baseline_i)"""
    return {
        feat: round(
            TECH_WEIGHTS[feat] * (feature_scores.get(feat, _TECH_BASELINES[feat]) - _TECH_BASELINES[feat]),
            4,
        )
        for feat in TECH_FEATURE_ORDER
    }


def tech_shap_summary(shap_vals: dict[str, float], candidate_name: str = "Candidate") -> str:
    sorted_feats = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    pos = [(f, v) for f, v in sorted_feats if v > 0.005][:2]
    neg = [(f, v) for f, v in sorted_feats if v < -0.005][:1]
    lines = [f"SHAP — Technical Score Drivers for {candidate_name}:"]
    lines += [f"  + {f}: +{v:.3f}" for f, v in pos]
    lines += [f"  - {f}: {v:.3f}"  for f, v in neg]
    return "\n".join(lines)


# ── Transcript parser (reuses same format as HR) ──────────────────────────────

def _extract_candidate_turns(transcript: str) -> list[str]:
    if not transcript.strip():
        return []
    candidate_pattern = re.compile(
        r"(?:candidate|applicant|interviewee)\s*:\s*(.+?)(?=\n(?:interviewer|hr|recruiter|agent)\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = candidate_pattern.findall(transcript)
    if matches:
        return [m.strip() for m in matches if m.strip()]
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    return [lines[i] for i in range(1, len(lines), 2)]


# ── Main entry point ──────────────────────────────────────────────────────────

def score_transcript(
    transcript: str,
    job_title: str,
    job_responsibilities: str,
    candidate_name: str = "Candidate",
) -> dict:
    """
    Score a technical interview transcript using 4 models.

    Returns composite_score (0–1) plus individual model scores.
    composite_score * 100 = overall_score to store in DB.

    Keys returned:
        codebert_score         — CodeBERT semantic alignment with JD
        nli_technical_score    — DeBERTa relevance to the role
        tfidf_technical_score  — technical keyword coverage of JD terms
        roberta_depth_score    — response confidence/quality
        composite_score        — weighted ensemble (0–1)
        overall_score_100      — composite * 100 (use as overall_score)
        shap_json              — JSON per-feature SHAP values
        shap_summary           — human-readable narrative
        turn_count             — number of candidate turns analysed
    """
    if not transcript or not transcript.strip():
        zero_feats = {f: 0.0 for f in TECH_FEATURE_ORDER}
        shap_vals  = compute_tech_shap(zero_feats)
        return {
            **zero_feats,
            "composite_score":    0.0,
            "overall_score_100":  0.0,
            "shap_json":          json.dumps(shap_vals),
            "shap_summary":       "No transcript available.",
            "turn_count":         0,
        }

    job_context = f"{job_title}: {job_responsibilities}"
    jd_combined = f"{job_title} {job_responsibilities}"

    turns = _extract_candidate_turns(transcript)
    if not turns:
        turns = [transcript[:2000]]

    nli_scores:      list[float] = []
    codebert_scores: list[float] = []
    quality_scores:  list[float] = []

    for turn_text in turns:
        if not turn_text.strip():
            continue
        nli_scores.append(_score_nli_alignment(turn_text, job_context))
        codebert_scores.append(_score_codebert(turn_text, jd_combined))
        quality_scores.append(_score_response_quality(turn_text))

    def _safe_mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    # TF-IDF uses the combined candidate text vs JD
    combined_candidate = " ".join(turns[:10])
    tfidf_score = _score_tfidf_coverage(combined_candidate, jd_combined)

    feature_scores: dict[str, float] = {
        "nli_technical_score":   _safe_mean(nli_scores),
        "codebert_score":        _safe_mean(codebert_scores),
        "tfidf_technical_score": tfidf_score,
        "roberta_depth_score":   _safe_mean(quality_scores),
    }

    composite = round(
        sum(TECH_WEIGHTS[f] * feature_scores[f] for f in TECH_FEATURE_ORDER), 4
    )
    overall_100 = round(composite * 100, 2)

    shap_vals = compute_tech_shap(feature_scores)
    summary   = tech_shap_summary(shap_vals, candidate_name)

    logger.info(
        "[tech_analysis] Transcript scored — turns=%d, nli=%.3f, codebert=%.3f, "
        "tfidf=%.3f, quality=%.3f → composite=%.3f (score=%.1f/100)",
        len(turns),
        feature_scores["nli_technical_score"],
        feature_scores["codebert_score"],
        feature_scores["tfidf_technical_score"],
        feature_scores["roberta_depth_score"],
        composite,
        overall_100,
    )

    return {
        **feature_scores,
        "composite_score":   composite,
        "overall_score_100": overall_100,
        "shap_json":         json.dumps(shap_vals),
        "shap_summary":      summary,
        "turn_count":        len(turns),
    }

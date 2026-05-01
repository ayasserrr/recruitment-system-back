"""
HR Analysis Service — Phase 8 Soft-Skills Scoring Engine
══════════════════════════════════════════════════════════

Analyzes candidate responses during HR interviews using 4 models:

  1. Go-Emotions   (SamLowe/roberta-base-go_emotions)                — emotion classification (diagnostic)
  2. RoBERTa Sent. (cardiffnlp/twitter-roberta-base-sentiment-latest) — tone / professionalism
  3. DeBERTa NLI   (cross-encoder/nli-deberta-v3-small)              — alignment with job context
  4. BGE bi-encoder (reused from embedding_service)                   — semantic depth vs JD

Composite score weights (Go-Emotions excluded from composite — stored as diagnostic signal only):
    nli_align_score:      0.55
    semantic_depth_score: 0.35
    sentiment_score:      0.10

Go-Emotions is excluded from the composite because it was trained on Reddit data and
introduces systematic bias against non-native speakers and culturally different expression styles.
It is still computed and stored on the session row for human review / audit.

SHAP is computed analytically (exact for linear models).

Usage (called from HR scoring pipeline after interview transcript is available):
    from services.hr_analysis_service import score_transcript

    result = score_transcript(
        transcript=session.transcript,
        job_title="Senior ML Engineer",
        job_responsibilities="Design and deploy ML pipelines...",
    )
    # result keys: emotion_score (diagnostic), sentiment_score, nli_align_score,
    #              semantic_depth_score, composite_score, shap_json, shap_summary
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from core.gpu import DEVICE  # sets HF_HOME env vars as a side-effect

logger = logging.getLogger(__name__)

# ── Model identifiers ─────────────────────────────────────────────────────────
_EMOTIONS_MODEL_ID  = "SamLowe/roberta-base-go_emotions"
_SENTIMENT_MODEL_ID = "cardiffnlp/twitter-roberta-base-sentiment-latest"
_NLI_MODEL_ID       = "cross-encoder/nli-deberta-v3-small"
_HF_CACHE           = "E:/huggingface_cache"

# ── Lazy singletons ───────────────────────────────────────────────────────────
_emotions_pipe:  "Pipeline | None" = None  # type: ignore[name-defined]
_sentiment_pipe: "Pipeline | None" = None  # type: ignore[name-defined]
_nli_pipe:       "Pipeline | None" = None  # type: ignore[name-defined]

# ── Positive emotions that signal confidence and cultural fit ─────────────────
_POSITIVE_EMOTIONS = frozenset({
    "admiration", "amusement", "approval", "caring", "curiosity",
    "excitement", "gratitude", "joy", "love", "optimism",
    "pride", "relief", "realization",
})

# ── HR scoring weights (emotion_score excluded — diagnostic only) ─────────────
HR_WEIGHTS: dict[str, float] = {
    "nli_align_score":      0.55,
    "semantic_depth_score": 0.35,
    "sentiment_score":      0.10,
}

_HR_BASELINES: dict[str, float] = {
    "nli_align_score":      0.40,
    "semantic_depth_score": 0.30,
    "sentiment_score":      0.50,
    # emotion_score baseline kept for diagnostic fallback only (not in composite)
    "emotion_score":        0.40,
}

HR_FEATURE_ORDER: list[str] = list(HR_WEIGHTS.keys())  # excludes emotion_score


# ── Lazy loaders ──────────────────────────────────────────────────────────────

def _get_emotions():
    global _emotions_pipe
    if _emotions_pipe is not None:
        return _emotions_pipe
    try:
        from transformers import pipeline
        device_id = 0 if DEVICE == "cuda" else -1
        _emotions_pipe = pipeline(
            "text-classification",
            model=_EMOTIONS_MODEL_ID,
            top_k=None,
            device=device_id,
            model_kwargs={"cache_dir": _HF_CACHE},
        )
        logger.info("[hr_analysis] Go-Emotions loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[hr_analysis] Go-Emotions unavailable: %s", exc)
    return _emotions_pipe


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
        logger.info("[hr_analysis] RoBERTa Sentiment loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[hr_analysis] RoBERTa Sentiment unavailable: %s", exc)
    return _sentiment_pipe


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
        logger.info("[hr_analysis] DeBERTa NLI loaded on device %d.", device_id)
    except Exception as exc:
        logger.warning("[hr_analysis] DeBERTa NLI unavailable: %s", exc)
    return _nli_pipe


# ── Individual model scorers ──────────────────────────────────────────────────

def _score_emotions(text: str) -> dict:
    """
    Go-Emotions multi-label classification on candidate text.

    Returns:
        emotion_score    — aggregate positive emotion probability (0-1)
        top_emotions     — top 3 emotion labels with scores (for reporting)
        raw_emotions     — full dict of all 28 emotion probabilities
    """
    pipe = _get_emotions()
    if pipe is None or not text.strip():
        return {"emotion_score": _HR_BASELINES["emotion_score"], "top_emotions": [], "raw_emotions": {}}
    try:
        results = pipe(text[:512])
        if not results:
            return {"emotion_score": _HR_BASELINES["emotion_score"], "top_emotions": [], "raw_emotions": {}}

        # Pipeline with top_k=None returns list of dicts [{label, score}, ...]
        label_scores = {r["label"]: r["score"] for r in results}

        # Aggregate positive emotions
        pos_score = sum(
            label_scores.get(e, 0.0) for e in _POSITIVE_EMOTIONS
        )
        # Normalize to [0, 1] — positive emotions rarely all fire at once
        emotion_score = round(min(1.0, pos_score), 4)

        top_3 = sorted(results, key=lambda x: x["score"], reverse=True)[:3]
        return {
            "emotion_score": emotion_score,
            "top_emotions":  [{"label": r["label"], "score": round(r["score"], 3)} for r in top_3],
            "raw_emotions":  {r["label"]: round(r["score"], 3) for r in results},
        }
    except Exception as exc:
        logger.warning("[hr_analysis] Emotion score failed: %s", exc)
        return {"emotion_score": _HR_BASELINES["emotion_score"], "top_emotions": [], "raw_emotions": {}}


def _score_sentiment(text: str) -> float:
    """
    RoBERTa Sentiment: returns a professionalism/positivity score in [0, 1].
    Maps: POSITIVE → 1.0, NEUTRAL → 0.5, NEGATIVE → 0.0, weighted by confidence.
    """
    pipe = _get_sentiment()
    if pipe is None or not text.strip():
        return _HR_BASELINES["sentiment_score"]
    try:
        result = pipe(text[:512])
        if not result:
            return _HR_BASELINES["sentiment_score"]
        label   = result[0]["label"].upper()
        conf    = float(result[0]["score"])
        mapping = {"POSITIVE": 1.0, "NEUTRAL": 0.5, "NEGATIVE": 0.0,
                   "POS": 1.0, "NEU": 0.5, "NEG": 0.0}
        raw     = mapping.get(label, 0.5)
        # Blend raw label with confidence (low-confidence POSITIVE is not as good)
        blended = 0.5 + (raw - 0.5) * conf
        return round(max(0.0, min(1.0, blended)), 4)
    except Exception as exc:
        logger.warning("[hr_analysis] Sentiment score failed: %s", exc)
        return _HR_BASELINES["sentiment_score"]


def _score_nli_alignment(candidate_text: str, job_context: str) -> float:
    """
    DeBERTa NLI: probability that the candidate's response is directly
    relevant to the stated job responsibilities/context.
    """
    pipe = _get_nli()
    if pipe is None or not candidate_text.strip() or not job_context.strip():
        return _HR_BASELINES["nli_align_score"]
    try:
        context_hint = (job_context[:100].strip() + "...") if len(job_context) > 100 else job_context
        result = pipe(
            candidate_text[:512],
            candidate_labels=["directly relevant to the role", "not relevant to the role"],
            hypothesis_template="This response is {} of: " + context_hint,
        )
        return round(float(result["scores"][0]), 4)
    except Exception as exc:
        logger.warning("[hr_analysis] NLI alignment score failed: %s", exc)
        return _HR_BASELINES["nli_align_score"]


def _score_semantic_depth(candidate_text: str, job_description: str) -> float:
    """
    BGE bi-encoder cosine similarity between candidate's response and the
    full job description — proxy for semantic depth and relevance.
    """
    try:
        from services.embedding_service import encode_jd, encode_cv, cosine_similarity
        jd_vec   = encode_jd(job_description)
        cand_vec = encode_cv(candidate_text)
        if not jd_vec or not cand_vec:
            return _HR_BASELINES["semantic_depth_score"]
        return round(max(0.0, min(1.0, cosine_similarity(jd_vec, cand_vec))), 4)
    except Exception as exc:
        logger.warning("[hr_analysis] Semantic depth score failed: %s", exc)
        return _HR_BASELINES["semantic_depth_score"]


# ── SHAP (analytical for linear model) ───────────────────────────────────────

def compute_hr_shap(feature_scores: dict[str, float]) -> dict[str, float]:
    """
    Exact SHAP values for the linear HR scoring function.
    phi_i = w_i * (x_i - baseline_i)
    Only covers the three composite features (emotion excluded).
    """
    return {
        feat: round(HR_WEIGHTS[feat] * (feature_scores.get(feat, _HR_BASELINES[feat]) - _HR_BASELINES[feat]), 4)
        for feat in HR_FEATURE_ORDER
    }


def hr_shap_summary(shap_vals: dict[str, float], candidate_name: str = "Candidate") -> str:
    """Human-readable SHAP narrative for the HR report."""
    sorted_feats = sorted(shap_vals.items(), key=lambda x: abs(x[1]), reverse=True)
    pos = [(f, v) for f, v in sorted_feats if v > 0.005][:2]
    neg = [(f, v) for f, v in sorted_feats if v < -0.005][:1]
    lines = [f"SHAP — HR Score Drivers for {candidate_name}:"]
    lines += [f"  + {f}: +{v:.3f}" for f, v in pos]
    lines += [f"  - {f}: {v:.3f}"  for f, v in neg]
    return "\n".join(lines)


# ── Transcript parser ─────────────────────────────────────────────────────────

def _extract_candidate_turns(transcript: str) -> list[str]:
    """
    Extract candidate-only turns from a transcript string.

    Supports two common transcript formats:
      1. "Candidate: <text>\nInterviewer: ..." (labeled turns)
      2. Alternating lines (every even line = interviewer, odd = candidate)

    Returns a list of candidate text segments.
    """
    if not transcript.strip():
        return []

    # Format 1: labeled turns
    candidate_pattern = re.compile(
        r"(?:candidate|applicant|interviewee)\s*:\s*(.+?)(?=\n(?:interviewer|hr|recruiter)\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = candidate_pattern.findall(transcript)
    if matches:
        return [m.strip() for m in matches if m.strip()]

    # Format 2: treat every other line as candidate (odd-indexed)
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    return [lines[i] for i in range(1, len(lines), 2)]  # 1, 3, 5, ... (assume interviewer first)


# ── Main entry point ──────────────────────────────────────────────────────────

def score_transcript(
    transcript:          str,
    job_title:           str,
    job_responsibilities: str,
    candidate_name:      str = "Candidate",
) -> dict:
    """
    Score a full HR interview transcript.

    Aggregates per-turn model scores across all candidate turns, then
    computes a composite HR score with SHAP explanation.

    Returns:
        emotion_score        — mean positive emotion across turns
        sentiment_score      — mean professionalism score across turns
        nli_align_score      — mean NLI alignment with job context
        semantic_depth_score — BGE cosine similarity vs full JD
        composite_score      — weighted ensemble (0-1)
        shap_json            — JSON string of per-feature SHAP values
        shap_summary         — human-readable narrative
        top_emotions         — list of dominant emotions detected
        turn_count           — number of candidate turns analyzed
    """
    if not transcript or not transcript.strip():
        zero_feats = {f: 0.0 for f in HR_FEATURE_ORDER}
        shap_vals  = compute_hr_shap(zero_feats)
        return {
            **zero_feats,
            "emotion_score":   0.0,   # diagnostic only — not in composite
            "composite_score": 0.0,
            "shap_json":       json.dumps(shap_vals),
            "shap_summary":    "No transcript available.",
            "top_emotions":    [],
            "turn_count":      0,
        }

    job_context = f"{job_title}: {job_responsibilities}"

    # ── Per-turn scoring ──────────────────────────────────────────────────────
    turns = _extract_candidate_turns(transcript)
    if not turns:
        # Fallback: treat entire transcript as one candidate turn
        turns = [transcript[:2000]]

    emotion_scores:   list[float] = []
    sentiment_scores: list[float] = []
    nli_scores:       list[float] = []
    all_top_emotions: list[dict]  = []

    for turn_text in turns:
        if not turn_text.strip():
            continue

        # Emotion — diagnostic only; stored but not included in composite
        emotion_result = _score_emotions(turn_text)
        emotion_scores.append(emotion_result["emotion_score"])
        all_top_emotions.extend(emotion_result.get("top_emotions", []))

        # Sentiment
        sentiment_scores.append(_score_sentiment(turn_text))

        # NLI alignment
        nli_scores.append(_score_nli_alignment(turn_text, job_context))

    def _safe_mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    # ── Semantic depth: use concatenation of all turns vs job description ─────
    combined_candidate_text = " ".join(turns[:10])  # cap at ~10 turns
    semantic_depth = _score_semantic_depth(combined_candidate_text, job_context)

    # Composite feature scores — emotion excluded to avoid demographic bias
    feature_scores: dict[str, float] = {
        "nli_align_score":      _safe_mean(nli_scores),
        "semantic_depth_score": semantic_depth,
        "sentiment_score":      _safe_mean(sentiment_scores),
    }
    # Emotion stored separately for human review (not in HR_FEATURE_ORDER)
    emotion_score_diagnostic = _safe_mean(emotion_scores)

    composite = round(
        sum(HR_WEIGHTS[f] * feature_scores[f] for f in HR_FEATURE_ORDER), 4
    )

    shap_vals = compute_hr_shap(feature_scores)
    summary   = hr_shap_summary(shap_vals, candidate_name)

    # Deduplicate top emotions by label, keep highest score
    seen_labels:      set[str]   = set()
    deduped_emotions: list[dict] = []
    for e in sorted(all_top_emotions, key=lambda x: x["score"], reverse=True):
        if e["label"] not in seen_labels:
            seen_labels.add(e["label"])
            deduped_emotions.append(e)
        if len(deduped_emotions) >= 5:
            break

    logger.info(
        "[hr_analysis] Transcript scored — turns=%d, nli=%.3f, depth=%.3f, "
        "sentiment=%.3f → composite=%.3f (emotion=%.3f diagnostic)",
        len(turns), feature_scores["nli_align_score"],
        feature_scores["semantic_depth_score"], feature_scores["sentiment_score"],
        composite, emotion_score_diagnostic,
    )

    return {
        **feature_scores,
        "emotion_score":   emotion_score_diagnostic,   # diagnostic — not in composite
        "composite_score": composite,
        "shap_json":       json.dumps(shap_vals),
        "shap_summary":    summary,
        "top_emotions":    deduped_emotions,
        "turn_count":      len(turns),
    }

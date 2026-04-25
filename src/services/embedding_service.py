"""
Embedding Service
─────────────────
Bi-Encoder + Cross-Encoder infrastructure for the recruitment pipeline.

Models (lazy-loaded on first call — no GPU startup cost at import time):
  Bi-Encoder:   intfloat/e5-large-v2   (1024-dim, MTEB retrieval avg 56.9)
                Requires symmetric "query: " / "passage: " prefixes.
  Cross-Encoder: BAAI/bge-reranker-v2-m3  (560MB, MTEB MRR@10 0.885, multilingual)
                Faster fallback: cross-encoder/ms-marco-MiniLM-L-12-v2

Key design decisions:
  • Models are module-level singletons — loaded once, reused across requests.
  • sigmoid() converts raw CE logits → probability before any blending.
  • hybrid_score() blends 70% CE probability with 30% rule-based skill_overlap
    so that skill-poor CVs with fluent writing cannot outscore skill-rich ones.
  • Both models degrade gracefully: if sentence-transformers is not installed,
    encode_jd / encode_cv / rerank return empty / zero values and log a warning.

Usage:
    from services.embedding_service import encode_jd, encode_cv, rerank, hybrid_score

    jd_vec  = encode_jd("Machine Learning Engineer with PyTorch experience...")
    cv_vec  = encode_cv("Experienced ML engineer skilled in PyTorch, NLP...")
    logits  = rerank(jd_text, [cv_text_1, cv_text_2])
    score   = hybrid_score(logits[0], skill_overlap=0.72)
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# ── Model identifiers ─────────────────────────────────────────────────────────
_BIENCODER_MODEL_ID  = "intfloat/e5-large-v2"
_CE_DEFAULT_MODEL_ID = "BAAI/bge-reranker-v2-m3"
_CE_FAST_MODEL_ID    = "cross-encoder/ms-marco-MiniLM-L-12-v2"

# ── Module-level singletons (None until first use) ────────────────────────────
_biencoder:    "SentenceTransformer | None" = None  # type: ignore[name-defined]
_crossencoder: "CrossEncoder | None"        = None  # type: ignore[name-defined]
_ce_model_id:  str                          = _CE_DEFAULT_MODEL_ID


# ─────────────────────────────────────────────────────────────────────────────
# Math utilities
# ─────────────────────────────────────────────────────────────────────────────

def sigmoid(x: float) -> float:
    """
    Convert a raw cross-encoder logit to a [0, 1] probability.

    BGE-reranker logits are typically in [-10, +10].  Applying sigmoid before
    blending with skill_overlap (which is already in [0, 1]) prevents the
    logit scale from dominating the hybrid score.
    """
    return 1.0 / (1.0 + math.exp(-x))


def hybrid_score(
    ce_logit: float,
    skill_overlap: float,
    alpha: float = 0.70,
) -> float:
    """
    Blend cross-encoder semantic probability with rule-based skill overlap.

    Args:
        ce_logit:      Raw logit from cross-encoder (BGE or MiniLM).
        skill_overlap: Fraction of JD required skills found in CV (0.0–1.0).
        alpha:         CE weight; (1-alpha) goes to skill_overlap. Default 0.70.

    Returns:
        Blended score in [0.0, 1.0].

    The 70/30 blend prevents two failure modes:
      1. Skill-poor CVs with fluent writing ranking above skill-rich terse CVs
         (pure semantic drift). The 30% skill_overlap anchors ranking in hard facts.
      2. Perfect skill matches with poor writing ranking above genuinely superior
         candidates (pure keyword stuffing). The 70% CE provides semantic context.
    """
    ce_prob = sigmoid(ce_logit)
    return round(alpha * ce_prob + (1.0 - alpha) * skill_overlap, 4)


def ce_logit_to_match_score(ce_logit: float) -> int:
    """Convert a CE logit to a 0–100 HR-readable match score."""
    return round(sigmoid(ce_logit) * 100)


def ce_score_to_label(ce_logit: float) -> str:
    """
    Convert raw CE logit to fit label using calibrated probability thresholds.
    Replaces the rank-position heuristic (_rank_position_labels) for better precision.
    """
    prob = sigmoid(ce_logit)
    if prob > 0.65:
        return "good_fit"
    if prob > 0.35:
        return "partial_fit"
    return "no_fit"


# ─────────────────────────────────────────────────────────────────────────────
# Bi-Encoder (intfloat/e5-large-v2)
# ─────────────────────────────────────────────────────────────────────────────

def _get_biencoder():
    """Lazy-load the bi-encoder; returns None if sentence-transformers not installed."""
    global _biencoder
    if _biencoder is not None:
        return _biencoder
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        logger.info(
            "[embedding_service] Loading bi-encoder: %s", _BIENCODER_MODEL_ID
        )
        _biencoder = SentenceTransformer(_BIENCODER_MODEL_ID)
        logger.info("[embedding_service] Bi-encoder ready.")
    except ImportError:
        logger.warning(
            "[embedding_service] sentence-transformers not installed — "
            "encode_jd/encode_cv will return empty vectors. "
            "Install with: pip install sentence-transformers"
        )
        _biencoder = None
    except Exception as exc:
        logger.error("[embedding_service] Failed to load bi-encoder: %s", exc)
        _biencoder = None
    return _biencoder


def encode_jd(jd_text: str) -> list[float]:
    """
    Encode a job description for retrieval.
    e5-large-v2 requires the "query: " prefix on the retrieval query side.
    Embeddings are L2-normalized (cosine similarity = dot product).
    """
    model = _get_biencoder()
    if model is None:
        logger.warning("[embedding_service] Bi-encoder unavailable — returning [].")
        return []
    prefixed = f"query: {jd_text.strip()}"
    vec = model.encode(prefixed, normalize_embeddings=True)
    return vec.tolist()


def encode_cv(cv_text: str) -> list[float]:
    """
    Encode a candidate CV for retrieval.
    e5-large-v2 requires the "passage: " prefix on the document side.
    """
    model = _get_biencoder()
    if model is None:
        logger.warning("[embedding_service] Bi-encoder unavailable — returning [].")
        return []
    prefixed = f"passage: {cv_text.strip()}"
    vec = model.encode(prefixed, normalize_embeddings=True)
    return vec.tolist()


def encode_cvs_batch(cv_texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Batch-encode a list of CV texts. More efficient than calling encode_cv in a loop."""
    model = _get_biencoder()
    if model is None:
        return [[] for _ in cv_texts]
    prefixed = [f"passage: {t.strip()}" for t in cv_texts]
    vecs = model.encode(prefixed, normalize_embeddings=True, batch_size=batch_size)
    return [v.tolist() for v in vecs]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity between two L2-normalized vectors.
    Since encode_jd/encode_cv use normalize_embeddings=True, this is just dot product.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return round(sum(x * y for x, y in zip(a, b)), 4)


def validate_embedding_quality(
    jd_vec: list[float],
    cv_vecs: list[list[float]],
    threshold: float = 0.55,
) -> None:
    """
    Sanity check: warn if the best cosine similarity is unexpectedly low.
    A score below 0.55 for the top candidate usually indicates a prefix
    misconfiguration or very short/empty full_text strings.
    """
    if not cv_vecs or not jd_vec:
        return
    best = max(cosine_similarity(jd_vec, cv) for cv in cv_vecs)
    if best < threshold:
        logger.warning(
            "[embedding_service] Best CV cosine=%.3f — below threshold %.2f. "
            "Check full_text quality and e5 query/passage prefix correctness.",
            best, threshold,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cross-Encoder (BAAI/bge-reranker-v2-m3)
# ─────────────────────────────────────────────────────────────────────────────

def _get_crossencoder(fast: bool = False):
    """
    Lazy-load the cross-encoder.
    fast=True loads MiniLM-L-12 (130MB, ~5x faster) for low-latency scenarios.
    fast=False loads BGE-reranker-v2-m3 (560MB, best accuracy).
    """
    global _crossencoder, _ce_model_id
    target_model = _CE_FAST_MODEL_ID if fast else _CE_DEFAULT_MODEL_ID

    # Reload if a different model variant is requested
    if _crossencoder is not None and _ce_model_id == target_model:
        return _crossencoder

    try:
        from sentence_transformers import CrossEncoder  # type: ignore
        logger.info("[embedding_service] Loading cross-encoder: %s", target_model)
        _crossencoder = CrossEncoder(target_model, max_length=512)
        _ce_model_id  = target_model
        logger.info("[embedding_service] Cross-encoder ready.")
    except ImportError:
        logger.warning(
            "[embedding_service] sentence-transformers not installed — "
            "rerank() will return zeros. Install with: pip install sentence-transformers"
        )
        _crossencoder = None
    except Exception as exc:
        logger.error("[embedding_service] Failed to load cross-encoder: %s", exc)
        _crossencoder = None

    return _crossencoder


def rerank(
    jd_text: str,
    cv_texts: list[str],
    fast: bool = False,
) -> list[float]:
    """
    Score each (jd_text, cv_text) pair with the cross-encoder.

    Returns raw logits (NOT probabilities) — apply sigmoid() before comparing
    with skill_overlap or converting to match_score.

    Args:
        jd_text:  Full job description text.
        cv_texts: List of candidate full_text strings (same order as candidates).
        fast:     Use the lighter MiniLM model for lower-latency scenarios.

    Returns:
        List of raw logit scores, same length as cv_texts.
        Returns [0.0] * len(cv_texts) if the model is unavailable.
    """
    if not cv_texts:
        return []

    model = _get_crossencoder(fast=fast)
    if model is None:
        logger.warning(
            "[embedding_service] Cross-encoder unavailable — returning zero logits."
        )
        return [0.0] * len(cv_texts)

    pairs = [(jd_text, cv) for cv in cv_texts]
    try:
        scores = model.predict(pairs)
        return [float(s) for s in scores]
    except Exception as exc:
        logger.error("[embedding_service] Cross-encoder predict failed: %s", exc)
        return [0.0] * len(cv_texts)


def rerank_and_sort(
    jd_text: str,
    candidates: list[dict],
    cv_text_key: str = "raw_text",
    fast: bool = False,
) -> list[dict]:
    """
    Convenience wrapper: rerank a list of candidate dicts and return them sorted
    by hybrid_score descending.  Adds 'ce_logit', 'ce_prob', 'hybrid_score'
    to each candidate dict (does not mutate original dicts).

    Args:
        candidates:   List of candidate dicts from the ranking pipeline.
        cv_text_key:  Key in each dict that holds the full CV text.
        fast:         Use MiniLM fallback model.
    """
    if not candidates:
        return []

    cv_texts = [c.get(cv_text_key, "") for c in candidates]
    logits   = rerank(jd_text, cv_texts, fast=fast)

    enriched = []
    for cand, logit in zip(candidates, logits):
        overlap = cand.get("skill_overlap", 0.0)
        enriched.append({
            **cand,
            "ce_logit":    round(logit, 4),
            "ce_prob":     round(sigmoid(logit), 4),
            "hybrid_score": hybrid_score(logit, overlap),
        })

    enriched.sort(key=lambda c: -c["hybrid_score"])
    return enriched

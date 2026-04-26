"""
Qdrant Service — Persistent Multi-Tenant Vector Store
──────────────────────────────────────────────────────
Wraps qdrant-client with the patterns needed for production use:

  1. Persistence:   disk-backed storage at ./qdrant_storage (survives restarts).
  2. Multi-tenancy: every point carries a "jd_id" payload field so a single
                    collection can serve all JDs without cross-contamination.
  3. Lazy init:     collection is created automatically on first upsert.
  4. Graceful degradation: if qdrant-client is not installed, all functions
                    return empty results and log a warning instead of crashing.

Usage:
    from services.qdrant_service import upsert_cv, search_for_jd, delete_jd_vectors

    # After encoding a CV:
    upsert_cv(cv_id=42, jd_id=7, vector=encode_cv(full_text), payload={"name": "Ali"})

    # Before cross-encoder reranking:
    hits = search_for_jd(jd_vector=encode_jd(jd_text), jd_id=7, top_k=20)
    # hits = [{"cv_id": 42, "score": 0.91, "name": "Ali"}, ...]
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
COLLECTION_NAME = "cv_embeddings"
VECTOR_DIM      = 1024          # e5-large-v2 / bge-large output dimension
STORAGE_PATH    = os.path.join(os.path.dirname(__file__), "..", "..", "qdrant_storage")

# ── Module-level client singleton ─────────────────────────────────────────────
_client: "QdrantClient | None" = None  # type: ignore[name-defined]


def _get_client():
    """
    Lazy-load QdrantClient with persistent disk storage.
    Returns None if qdrant-client is not installed.
    """
    global _client
    if _client is not None:
        return _client

    try:
        from qdrant_client import QdrantClient  # type: ignore
        storage = os.path.abspath(STORAGE_PATH)
        os.makedirs(storage, exist_ok=True)
        _client = QdrantClient(path=storage)
        logger.info("[qdrant_service] Client initialized. Storage: %s", storage)
    except ImportError:
        logger.warning(
            "[qdrant_service] qdrant-client not installed — vector search disabled. "
            "Install with: pip install qdrant-client"
        )
        _client = None
    except Exception as exc:
        logger.error("[qdrant_service] Failed to initialize Qdrant client: %s", exc)
        _client = None

    return _client


def _ensure_collection() -> bool:
    """
    Create the collection if it does not exist.
    Returns True on success, False if client unavailable.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        from qdrant_client.models import Distance, VectorParams  # type: ignore
        if not client.collection_exists(COLLECTION_NAME):
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info(
                "[qdrant_service] Collection '%s' created (dim=%d, COSINE).",
                COLLECTION_NAME, VECTOR_DIM,
            )
        return True
    except Exception as exc:
        logger.error("[qdrant_service] Failed to ensure collection: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def upsert_cv(
    cv_id: int,
    jd_id: int,
    vector: list[float],
    payload: Optional[dict] = None,
) -> bool:
    """
    Insert or update a CV embedding with its jd_id tag.

    The jd_id payload field is the key to multi-tenant isolation:
    search_for_jd() filters on this field so a JD only retrieves its own CVs.

    Args:
        cv_id:   Unique point ID (use application_id or cv_id from DB).
        jd_id:   Job description / requisition ID — used as the filter key.
        vector:  Embedding from encode_cv() — must be VECTOR_DIM floats.
        payload: Additional metadata to store (candidate name, score, etc.).

    Returns:
        True on success, False on failure.
    """
    if not vector:
        logger.warning("[qdrant_service] upsert_cv called with empty vector — skipping.")
        return False

    client = _get_client()
    if client is None or not _ensure_collection():
        return False

    try:
        from qdrant_client.models import PointStruct  # type: ignore
        point_payload = {"jd_id": jd_id, **(payload or {})}
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=cv_id, vector=vector, payload=point_payload)],
        )
        logger.debug("[qdrant_service] Upserted cv_id=%d for jd_id=%d.", cv_id, jd_id)
        return True
    except Exception as exc:
        logger.error(
            "[qdrant_service] upsert_cv failed for cv_id=%d, jd_id=%d: %s",
            cv_id, jd_id, exc,
        )
        return False


def upsert_cvs_batch(
    points: list[dict],
) -> int:
    """
    Batch upsert multiple CV embeddings for efficiency.

    Each element of `points` must be a dict with keys:
        cv_id, jd_id, vector, payload (optional)

    Returns:
        Number of points successfully upserted.
    """
    client = _get_client()
    if client is None or not _ensure_collection():
        return 0

    try:
        from qdrant_client.models import PointStruct  # type: ignore
        qdrant_points = [
            PointStruct(
                id=p["cv_id"],
                vector=p["vector"],
                payload={"jd_id": p["jd_id"], **(p.get("payload") or {})},
            )
            for p in points
            if p.get("vector")
        ]
        if not qdrant_points:
            return 0
        client.upsert(collection_name=COLLECTION_NAME, points=qdrant_points)
        logger.info("[qdrant_service] Batch upserted %d points.", len(qdrant_points))
        return len(qdrant_points)
    except Exception as exc:
        logger.error("[qdrant_service] Batch upsert failed: %s", exc)
        return 0


def search_for_jd(
    jd_vector: list[float],
    jd_id: int,
    top_k: int = 20,
) -> list[dict]:
    """
    Retrieve the top-K most similar CV embeddings for a specific JD.

    Uses the jd_id payload filter so only CVs uploaded for this JD are
    considered — full multi-tenant isolation.

    Args:
        jd_vector: Embedding from encode_jd() — VECTOR_DIM floats.
        jd_id:     Filter — only return points with payload.jd_id == jd_id.
        top_k:     Maximum number of results (default 20 = TOP_K).

    Returns:
        List of dicts: [{"cv_id": int, "score": float, ...payload fields}]
        Empty list on failure or if qdrant-client unavailable.
    """
    if not jd_vector:
        return []

    client = _get_client()
    if client is None or not _ensure_collection():
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue  # type: ignore
        qfilter = Filter(
            must=[FieldCondition(key="jd_id", match=MatchValue(value=jd_id))]
        )

        # qdrant-client ≥1.7 removed client.search() — use query_points() instead.
        # client.search() is kept as a fallback for installations still on <1.7.
        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=COLLECTION_NAME,
                query=jd_vector,
                query_filter=qfilter,
                limit=top_k,
                with_payload=True,
            )
            raw_hits = response.points
        else:
            raw_hits = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=jd_vector,
                query_filter=qfilter,
                limit=top_k,
                with_payload=True,
            )

        results = [
            {"cv_id": h.id, "score": round(h.score, 4), **(h.payload or {})}
            for h in raw_hits
        ]
        logger.info(
            "[qdrant_service] search_for_jd jd_id=%d — retrieved %d candidates (top_k=%d).",
            jd_id, len(results), top_k,
        )
        return results
    except Exception as exc:
        logger.error("[qdrant_service] search_for_jd failed for jd_id=%d: %s", jd_id, exc)
        return []


def delete_jd_vectors(jd_id: int) -> int:
    """
    Remove all CV embeddings tagged with jd_id.
    Call this when a requisition is deleted or reset.

    Returns:
        Number of points deleted, or -1 on error.
    """
    client = _get_client()
    if client is None:
        return -1

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue  # type: ignore
        qfilter = Filter(
            must=[FieldCondition(key="jd_id", match=MatchValue(value=jd_id))]
        )

        # qdrant-client ≥1.7 requires a FilterSelector wrapper around the Filter
        # when used as points_selector; older clients accept Filter directly.
        try:
            from qdrant_client.models import FilterSelector  # type: ignore
            selector = FilterSelector(filter=qfilter)
        except ImportError:
            selector = qfilter  # type: ignore[assignment]  # pre-1.7 fallback

        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=selector,
        )
        logger.info("[qdrant_service] Deleted vectors for jd_id=%d.", jd_id)
        return 0
    except Exception as exc:
        logger.error("[qdrant_service] delete_jd_vectors failed for jd_id=%d: %s", jd_id, exc)
        return -1


def collection_stats() -> dict:
    """Return basic collection statistics for health monitoring."""
    client = _get_client()
    if client is None:
        return {"status": "unavailable"}
    try:
        info = client.get_collection(COLLECTION_NAME)
        return {
            "status":         "ok",
            "vectors_count":  info.vectors_count,
            "points_count":   info.points_count,
            "collection":     COLLECTION_NAME,
            "vector_dim":     VECTOR_DIM,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}

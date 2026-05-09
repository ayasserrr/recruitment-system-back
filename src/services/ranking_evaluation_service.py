"""
Offline ranking evaluation framework.

Computes NDCG@K, MRR, Recall@K, Precision@K, and F1@K against ground-truth
relevance labels for a requisition.  Ported from the Phase-1 research notebook
(final-phase1-eman-mahmoud.ipynb) and adapted for production DB schema.

Schema notes:
- Rankings are stored in SemanticAnalysisReport (one row per application).
  Columns used: rank_in_pool, match_percentage, recommendation_summary.
- The fit label (good_fit / partial_fit / no_fit) is embedded at the start of
  recommendation_summary as "Pool Label: <label> (".  We parse it out here.
- Applications are linked to a requisition via job_postings.requisition_id,
  not directly — Application has no requisition_id column.

Usage (from a Celery task or admin route):
    from services.ranking_evaluation_service import evaluate_requisition
    report = evaluate_requisition(db, requisition_id=3, k=10)
    print(report)
"""

import logging
import math
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.db.application import Application
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport

logger = logging.getLogger(__name__)

# ── Core metric functions ──────────────────────────────────────────────────────

def dcg_at_k(relevances: List[float], k: int) -> float:
    total = 0.0
    for i, rel in enumerate(relevances[:k], start=1):
        total += rel / math.log2(i + 1)
    return total


def ndcg_at_k(relevances: List[float], ideal_relevances: List[float], k: int) -> float:
    idcg = dcg_at_k(sorted(ideal_relevances, reverse=True), k)
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(relevances, k) / idcg


def mrr(relevances: List[float]) -> float:
    for i, rel in enumerate(relevances, start=1):
        if rel > 0:
            return 1.0 / i
    return 0.0


def recall_at_k(relevances: List[float], total_relevant: int, k: int) -> float:
    if total_relevant == 0:
        return 0.0
    return sum(1 for r in relevances[:k] if r > 0) / total_relevant


def precision_at_k(relevances: List[float], k: int) -> float:
    if k == 0:
        return 0.0
    return sum(1 for r in relevances[:k] if r > 0) / k


def f1_at_k(relevances: List[float], total_relevant: int, k: int) -> float:
    p = precision_at_k(relevances, k)
    r = recall_at_k(relevances, total_relevant, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


# ── Label extraction ──────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"Pool Label:\s*(good_fit|partial_fit|no_fit)", re.IGNORECASE)


def _extract_label(recommendation_summary: Optional[str]) -> Optional[str]:
    """Parse 'Pool Label: good_fit ...' from recommendation_summary text."""
    if not recommendation_summary:
        return None
    m = _LABEL_RE.search(recommendation_summary)
    return m.group(1).lower() if m else None


def _graded_relevance(label: Optional[str]) -> float:
    """good_fit→2, partial_fit→1, no_fit→0 (graded for NDCG)."""
    return {"good_fit": 2.0, "partial_fit": 1.0, "no_fit": 0.0}.get(label or "", 0.0)


def _binary_relevance(label: Optional[str]) -> float:
    """good_fit→1, anything else→0 (for Recall/Precision/F1)."""
    return 1.0 if (label or "") == "good_fit" else 0.0


# ── Main evaluation entry point ────────────────────────────────────────────────

def evaluate_requisition(
    db: Session,
    requisition_id: int,
    k: int = 10,
) -> Dict:
    """
    Evaluate ranking quality for all applications under a requisition.

    Returns NDCG@K, MRR, Recall@K, Precision@K, F1@K, and per-application
    rank details.  Requires Phase-1 ranking to have run (SemanticAnalysisReport
    rows must exist).
    """
    jr: Optional[JobRequisition] = (
        db.query(JobRequisition)
        .filter(JobRequisition.requisition_id == requisition_id)
        .first()
    )
    if not jr:
        raise ValueError(f"JobRequisition {requisition_id} not found.")

    # Applications → PostingID → requisition_id (no direct FK on Application)
    reports: List[SemanticAnalysisReport] = (
        db.query(SemanticAnalysisReport)
        .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
        .join(JobPosting, JobPosting.posting_id == Application.posting_id)
        .filter(JobPosting.requisition_id == requisition_id)
        .filter(SemanticAnalysisReport.rank_in_pool.isnot(None))
        .order_by(SemanticAnalysisReport.rank_in_pool.asc())
        .all()
    )

    if not reports:
        logger.warning("[eval] No ranked SemanticAnalysisReport rows for requisition %d.", requisition_id)
        return {
            "requisition_id": requisition_id,
            "job_title": jr.job_title or "N/A",
            "k": k,
            "n_candidates": 0,
            "n_relevant": 0,
            "ndcg_at_k": None,
            "mrr": None,
            "recall_at_k": None,
            "precision_at_k": None,
            "f1_at_k": None,
            "details": [],
        }

    labels = [_extract_label(r.recommendation_summary) for r in reports]
    graded = [_graded_relevance(lbl) for lbl in labels]
    binary = [_binary_relevance(lbl) for lbl in labels]
    ideal_graded = sorted(graded, reverse=True)
    total_relevant = int(sum(binary))

    metrics = {
        "requisition_id": requisition_id,
        "job_title": jr.job_title or "N/A",
        "k": k,
        "n_candidates": len(reports),
        "n_relevant": total_relevant,
        "ndcg_at_k": round(ndcg_at_k(graded, ideal_graded, k), 4),
        "mrr": round(mrr(binary), 4),
        "recall_at_k": round(recall_at_k(binary, total_relevant, k), 4),
        "precision_at_k": round(precision_at_k(binary, k), 4),
        "f1_at_k": round(f1_at_k(binary, total_relevant, k), 4),
        "details": [
            {
                "rank": r.rank_in_pool,
                "application_id": r.application_id,
                "label": labels[i],
                "score": round(float(r.match_percentage or 0), 2),
                "graded_relevance": graded[i],
            }
            for i, r in enumerate(reports)
        ],
    }

    logger.info(
        "[eval] Requisition %d (%s) | n=%d rel=%d | NDCG@%d=%.4f MRR=%.4f P@%d=%.4f R@%d=%.4f F1@%d=%.4f",
        requisition_id, jr.job_title,
        len(reports), total_relevant,
        k, metrics["ndcg_at_k"],
        metrics["mrr"],
        k, metrics["precision_at_k"],
        k, metrics["recall_at_k"],
        k, metrics["f1_at_k"],
    )

    return metrics


# ── Multi-requisition batch evaluation ────────────────────────────────────────

def evaluate_all_requisitions(db: Session, k: int = 10) -> Dict:
    """
    Run evaluate_requisition for every JobRequisition that has ranked applications.
    Returns macro-averaged metrics plus per-requisition breakdown.
    """
    all_jrs: List[JobRequisition] = db.query(JobRequisition).all()

    results = []
    for jr in all_jrs:
        try:
            r = evaluate_requisition(db, jr.requisition_id, k=k)
            if r["n_candidates"] > 0:
                results.append(r)
        except Exception as exc:
            logger.warning("[eval] Skipping requisition %d: %s", jr.requisition_id, exc)

    if not results:
        return {"k": k, "n_requisitions": 0, "macro_avg": {}, "per_requisition": []}

    def _avg(key: str) -> float:
        vals = [r[key] for r in results if r[key] is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "k": k,
        "n_requisitions": len(results),
        "macro_avg": {
            "ndcg_at_k": _avg("ndcg_at_k"),
            "mrr": _avg("mrr"),
            "recall_at_k": _avg("recall_at_k"),
            "precision_at_k": _avg("precision_at_k"),
            "f1_at_k": _avg("f1_at_k"),
        },
        "per_requisition": results,
    }

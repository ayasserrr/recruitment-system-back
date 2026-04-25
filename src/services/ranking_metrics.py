"""
Ranking Evaluation Metrics
──────────────────────────
Implementation of retrieval / ranking evaluation metrics derived from the
Gold Standard notebook (Phase 3 evaluation pipeline).

Metrics:
  • NDCG@K  — Normalized Discounted Cumulative Gain
  • MRR     — Mean Reciprocal Rank
  • P@K     — Precision at K
  • R@K     — Recall at K
  • F1@K    — F1 at K

Label encoding (mirrors Gold Standard LABEL_SCORE):
  good_fit    → 2
  partial_fit → 1
  no_fit      → 0

Usage:
  from services.ranking_metrics import evaluate_ranking

  metrics = evaluate_ranking(
      ranked_candidate_ids=["app_5", "app_2", "app_9"],
      true_labels={"app_5": "good_fit", "app_2": "no_fit", "app_9": "partial_fit"},
      k=10,
  )
"""

from __future__ import annotations

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Label → relevance score (Gold Standard LABEL_SCORE) ───────────────────────
LABEL_SCORE: dict[str, int] = {
    "good_fit":    2,
    "partial_fit": 1,
    "no_fit":      0,
}

# Map our pool-relative labels to the gold-standard equivalents for metric computation
POOL_LABEL_TO_FIT: dict[str, str] = {
    "Top Candidate":   "good_fit",
    "Strong Runner-Up":"good_fit",
    "Strong Hire":     "good_fit",
    "Hire":            "partial_fit",
    "Maybe":           "partial_fit",
    "No Hire":         "no_fit",
    "Borderline":      "partial_fit",
}


# ─────────────────────────────────────────────────────────────────────────────
# Core metric functions
# ─────────────────────────────────────────────────────────────────────────────

def dcg_at_k(relevances: list[int], k: int) -> float:
    """
    Discounted Cumulative Gain at K.
    DCG@K = Σ (2^rel_i - 1) / log2(i + 2)  for i in 0..k-1
    """
    return sum(
        (2 ** rel - 1) / math.log2(i + 2)
        for i, rel in enumerate(relevances[:k])
    )


def ndcg_at_k(
    ranked_ids: list[str],
    true_labels: dict[str, str],
    k: int = 10,
) -> float:
    """
    Normalized Discounted Cumulative Gain at K.

    Args:
        ranked_ids:   Candidate IDs in ranked order (position 0 = rank 1).
        true_labels:  {candidate_id: label} ground-truth labels.
        k:            Cut-off depth.

    Returns:
        NDCG@K in [0.0, 1.0]. Returns 0.0 if ideal DCG is 0.
    """
    pred_relevances = [
        LABEL_SCORE.get(true_labels.get(cid, "no_fit"), 0)
        for cid in ranked_ids
    ]
    ideal_relevances = sorted(pred_relevances, reverse=True)

    dcg  = dcg_at_k(pred_relevances, k)
    idcg = dcg_at_k(ideal_relevances, k)

    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def mean_reciprocal_rank(
    ranked_ids: list[str],
    true_labels: dict[str, str],
    relevant_label: str = "good_fit",
) -> float:
    """
    Mean Reciprocal Rank — reciprocal of the rank of the first relevant result.

    Args:
        ranked_ids:     Candidate IDs in ranked order.
        true_labels:    {candidate_id: label} ground-truth.
        relevant_label: Label considered "relevant" (default: "good_fit").

    Returns:
        MRR in (0.0, 1.0]. 0.0 if no relevant result is found.
    """
    for rank, cid in enumerate(ranked_ids, start=1):
        if true_labels.get(cid, "no_fit") == relevant_label:
            return round(1.0 / rank, 4)
    return 0.0


def precision_recall_f1(
    ranked_ids: list[str],
    true_labels: dict[str, str],
    k: int = 10,
    relevant_label: str = "good_fit",
) -> tuple[float, float, float]:
    """
    Precision@K, Recall@K, and F1@K.

    Args:
        ranked_ids:     Candidate IDs in ranked order.
        true_labels:    {candidate_id: label} ground-truth.
        k:              Cut-off depth for precision.
        relevant_label: Label considered "relevant" (default: "good_fit").

    Returns:
        (precision@k, recall@k, f1@k) all in [0.0, 1.0].
    """
    top_k = ranked_ids[:k]
    tp = sum(1 for cid in top_k if true_labels.get(cid, "no_fit") == relevant_label)
    total_relevant = sum(1 for lbl in true_labels.values() if lbl == relevant_label)

    precision = tp / k if k > 0 else 0.0
    recall    = tp / total_relevant if total_relevant > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return round(precision, 4), round(recall, 4), round(f1, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience evaluator
# ─────────────────────────────────────────────────────────────────────────────

def classification_metrics(
    true_labels: list[str],
    pred_labels: list[str],
    positive_class: str = "good_fit",
) -> dict:
    """
    Binary classification metrics treating positive_class vs. everything else.

    Args:
        true_labels:    Ground-truth label per candidate.
        pred_labels:    Predicted label per candidate (same order).
        positive_class: Label treated as the positive class.

    Returns:
        Dict with accuracy, precision, recall, f1.
    """
    if not true_labels or len(true_labels) != len(pred_labels):
        return {"error": "mismatched or empty label lists"}

    tp = sum(1 for t, p in zip(true_labels, pred_labels) if t == positive_class and p == positive_class)
    fp = sum(1 for t, p in zip(true_labels, pred_labels) if t != positive_class and p == positive_class)
    fn = sum(1 for t, p in zip(true_labels, pred_labels) if t == positive_class and p != positive_class)
    correct = sum(1 for t, p in zip(true_labels, pred_labels) if t == p)

    n = len(true_labels)
    accuracy  = round(correct / n, 4)
    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall    = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall) > 0 else 0.0
    )

    return {
        "accuracy":  accuracy,
        "precision": precision,
        "recall":    recall,
        "f1":        f1,
        "tp": tp, "fp": fp, "fn": fn,
    }


def _rank_position_labels(ranked_ids: list[str]) -> dict[str, str]:
    """
    Derive predicted labels from rank position (notebook Fix v2).
    Top-33% → good_fit, next-33% → partial_fit, bottom-33% → no_fit.
    """
    n = len(ranked_ids)
    top_cut    = max(1, n // 3)
    mid_cut    = max(top_cut + 1, 2 * n // 3)
    pred: dict[str, str] = {}
    for i, cid in enumerate(ranked_ids):
        if i < top_cut:
            pred[cid] = "good_fit"
        elif i < mid_cut:
            pred[cid] = "partial_fit"
        else:
            pred[cid] = "no_fit"
    return pred


def evaluate_ranking(
    ranked_candidate_ids: list[str],
    true_labels: dict[str, str],
    k: int = 10,
) -> dict:
    """
    Run all metrics on a ranked list of candidates.

    Args:
        ranked_candidate_ids: IDs in rank order (best first).
        true_labels:          {candidate_id → "good_fit"|"partial_fit"|"no_fit"}.
        k:                    Evaluation cut-off depth.

    Returns:
        Dict with all metric values, classification metrics, and per-label counts.
    """
    if not ranked_candidate_ids:
        return {"error": "empty ranked list"}

    ndcg  = ndcg_at_k(ranked_candidate_ids, true_labels, k)
    mrr   = mean_reciprocal_rank(ranked_candidate_ids, true_labels)
    p, r, f1 = precision_recall_f1(ranked_candidate_ids, true_labels, k)

    # Fix v2: derive predicted labels from rank position, not from true_labels
    pred_map   = _rank_position_labels(ranked_candidate_ids)
    true_flat  = [true_labels.get(cid, "no_fit") for cid in ranked_candidate_ids]
    pred_flat  = [pred_map[cid] for cid in ranked_candidate_ids]
    clf        = classification_metrics(true_flat, pred_flat)

    label_counts = {lbl: 0 for lbl in LABEL_SCORE}
    for lbl in true_labels.values():
        norm = lbl.lower()
        if norm in label_counts:
            label_counts[norm] += 1

    logger.info(
        "[ranking_metrics] k=%d  NDCG=%.4f  MRR=%.4f  P@k=%.4f  R@k=%.4f  F1@k=%.4f  "
        "Clf-Acc=%.4f  Clf-F1=%.4f",
        k, ndcg, mrr, p, r, f1, clf.get("accuracy", 0), clf.get("f1", 0),
    )

    return {
        "k":          k,
        "ndcg_at_k":  ndcg,
        "mrr":        mrr,
        "precision_at_k": p,
        "recall_at_k":    r,
        "f1_at_k":        f1,
        "classification": clf,
        "label_distribution": label_counts,
        "pool_size":  len(ranked_candidate_ids),
    }


def evaluate_from_ranked_candidates(
    ranked_candidates: list[dict],
    k: int = 10,
) -> dict:
    """
    Evaluate a ranked_candidates list from run_ranking_graph() output.
    Converts pool-relative labels to fit labels automatically.

    ranked_candidates: list of candidate dicts with 'application_id' and 'recommendation'.
    """
    ranked_ids = [str(c.get("application_id", i)) for i, c in enumerate(ranked_candidates)]
    true_labels = {
        str(c.get("application_id", i)): POOL_LABEL_TO_FIT.get(
            c.get("recommendation", "No Hire"), "no_fit"
        )
        for i, c in enumerate(ranked_candidates)
    }
    return evaluate_ranking(ranked_ids, true_labels, k)

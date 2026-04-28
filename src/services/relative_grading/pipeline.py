"""
3-Phase Relative Grading Pipeline
──────────────────────────────────
Phase 1 — Keyword coverage with GPT-4o-mini semantic fallback
Phase 2 — Comparative depth scoring (normalised against pool maximum)
Phase 3 — Ranking, rejection, and deterministic GPT-4o tiebreaking

Entry point: run_pipeline(candidates, concepts, config)
"""
from __future__ import annotations

import logging
from statistics import mean
from typing import Optional

from services.relative_grading.grading_config import GradingConfig, load_config
from services.relative_grading.models import (
    DepthScorer,
    Lemmatizer,
    SemanticModel,
    TiebreakerModel,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Keyword Coverage
# ─────────────────────────────────────────────────────────────────────────────

def phase1_score(
    answer_text: str,
    required_kws: list[str],
    lemmatizer: Lemmatizer,
    semantic_model: SemanticModel,
    config: GradingConfig,
) -> dict:
    """
    Returns:
      score, matched, missing, semantic_recovered, match_count
    """
    if not required_kws:
        # sentinel match_count=1 so normalize_pool yields 1.0 coverage instead of 0/0
        return {
            "score": 1.0,
            "matched": [],
            "missing": [],
            "semantic_recovered": [],
            "match_count": 1,
        }

    answer_lemmas = lemmatizer.lemmatize(answer_text)
    matched: list[str] = []
    missing: list[str] = []

    for kw in required_kws:
        kw_lemmas = lemmatizer.lemmatize(kw)
        # All tokens of the keyword appear in the answer lemma set
        if kw_lemmas and kw_lemmas.issubset(answer_lemmas):
            matched.append(kw)
        # Raw substring fallback (handles multi-token keywords like "event loop")
        elif kw.lower() in answer_text.lower():
            matched.append(kw)
        else:
            missing.append(kw)

    # Semantic fallback via GPT-4o-mini batched call
    semantic_recovered: list[str] = []
    if missing:
        recovery_map = semantic_model.batch_semantic_check(answer_text, missing)
        still_missing: list[str] = []
        for kw in missing:
            if recovery_map.get(kw, False):
                semantic_recovered.append(kw)
            else:
                still_missing.append(kw)
        missing = still_missing

    match_count = len(matched) + len(semantic_recovered)
    score = match_count / len(required_kws) if required_kws else 1.0

    return {
        "score": round(score, 4),
        "matched": matched,
        "missing": missing,
        "semantic_recovered": semantic_recovered,
        "match_count": match_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Comparative Scoring
# ─────────────────────────────────────────────────────────────────────────────

def normalize_pool(all_phase1: dict[str, dict]) -> dict[str, float]:
    """
    Normalise match_count against the pool maximum for a single concept.

    all_phase1: {candidate_name → phase1_result_dict}
    Returns:    {candidate_name → coverage_norm (0–1)}
    """
    if not all_phase1:
        return {}
    max_match = max(r["match_count"] for r in all_phase1.values())
    if max_match == 0:
        return {name: 0.0 for name in all_phase1}
    return {
        name: round(r["match_count"] / max_match, 4)
        for name, r in all_phase1.items()
    }


def depth_score(
    question_text: str,
    answer_text: str,
    depth_scorer: DepthScorer,
) -> float:
    return depth_scorer.score(question_text, answer_text)


def comparative_score(
    coverage_norm: float,
    d_score: float,
    config: GradingConfig,
) -> float:
    return round(
        (coverage_norm * config.weights.coverage) + (d_score * config.weights.depth),
        4,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Ranking, Rejection, Tiebreaking
# ─────────────────────────────────────────────────────────────────────────────

def final_candidate_score(comparative_scores: list[float]) -> float:
    return round(mean(comparative_scores), 4) if comparative_scores else 0.0


def rejection_logic(
    final_score: float,
    avg_depth: float,
    pass_threshold: float,
) -> tuple[bool, Optional[str]]:
    """
    Depth compensates for low coverage:
      reject only when BOTH final_score AND avg_depth are below threshold.
    """
    if final_score < pass_threshold and avg_depth <= pass_threshold:
        return True, "Low keyword coverage and insufficient explanation depth"
    return False, None


def segmentation(final_score: float, config: GradingConfig) -> str:
    if final_score >= config.segments.shortlist:
        return "✅ Human Review"
    if final_score >= config.segments.pass_threshold:
        return "🟡 Shortlist"
    return "❌ Auto-Reject"


def _tiebreak(
    name_a: str,
    answer_a: str,
    name_b: str,
    answer_b: str,
    question_text: str,
    tiebreaker_model: TiebreakerModel,
    config: GradingConfig,
) -> str:
    winner_letter = tiebreaker_model.compare(
        question_text=question_text,
        answer_a=answer_a,
        answer_b=answer_b,
        runs=config.tiebreaker.runs,
        temperature=config.tiebreaker.temperature,
    )
    return name_a if winner_letter == "A" else name_b


# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline Entry
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    candidates: list[dict],
    concepts: list[dict],
    config: Optional[GradingConfig] = None,
) -> tuple[list[dict], dict]:
    """
    Parameters
    ──────────
    candidates : output of data_loader.load_candidates()
    concepts   : output of data_loader.load_concepts()
    config     : loaded GradingConfig (or None → loads from config.yaml)

    Returns
    ───────
    (leaderboard, keyword_gap_report)

    leaderboard : list[dict] — passing first, then rejected; each entry has
                  rank, name, candidate_id, application_id, assessment_id,
                  final_score, avg_depth, reject, reject_reason, segment,
                  per_question (dict of concept → per-question detail)

    keyword_gap_report : {concept_name → list[missed_keywords] | "all covered"}
    """
    if config is None:
        config = load_config()

    if not candidates:
        logger.warning("[pipeline] No submitted candidates — nothing to grade.")
        return [], {}

    if not concepts:
        logger.warning("[pipeline] No concepts/questions found — cannot grade.")
        return [], {}

    lemmatizer = Lemmatizer()
    semantic_model = SemanticModel()
    depth_scorer_model = DepthScorer()
    tiebreaker_model = TiebreakerModel()

    # ── Phase 1 + 2 per concept ──────────────────────────────────────────────
    # concept_results[concept_name][candidate_name] = {phase1, answer, depth_score,
    #                                                   coverage_norm, comparative_score}
    concept_results: dict[str, dict[str, dict]] = {}
    # covered_kws_by_concept[concept_name] = set of keywords covered by at least one candidate
    covered_kws_by_concept: dict[str, set[str]] = {}

    # MCQ max points per question (score_awarded scale is 0–10)
    _MCQ_MAX = 10.0

    for concept in concepts:
        cname = concept["concept_name"]
        q_text = concept["question_text"]
        req_kws = concept["required_keywords"]
        tq_id = concept["template_question_id"]
        is_mcq = concept.get("question_type", "open_ended").lower() == "mcq"

        concept_results[cname] = {}
        covered_kws_by_concept[cname] = set()

        if is_mcq:
            # ── MCQ: use pre-computed score_awarded, skip P1/P2 entirely ─────
            for cand in candidates:
                raw = cand.get("mcq_scores", {}).get(tq_id, 0.0)
                norm = round(min(raw / _MCQ_MAX, 1.0), 4)
                concept_results[cname][cand["name"]] = {
                    "phase1": {"score": norm, "matched": [], "missing": [],
                               "semantic_recovered": [], "match_count": int(norm)},
                    "answer": cand["answers"].get(tq_id, ""),
                    "depth_score": norm,        # MCQ depth = correctness
                    "coverage_norm": norm,
                    "comparative_score": norm,
                }
                logger.info(
                    "[MCQ] %s | %s score_awarded=%.1f → norm=%.2f",
                    cand["name"], cname, raw, norm,
                )
            continue  # skip P1/P2 loop below

        # ── Phase 1 for every candidate (open-ended only) ────────────────────
        for cand in candidates:
            answer_text = cand["answers"].get(tq_id, "")
            p1 = phase1_score(
                answer_text=answer_text,
                required_kws=req_kws,
                lemmatizer=lemmatizer,
                semantic_model=semantic_model,
                config=config,
            )
            concept_results[cname][cand["name"]] = {
                "phase1": p1,
                "answer": answer_text,
            }
            covered_kws_by_concept[cname].update(p1["matched"])
            covered_kws_by_concept[cname].update(p1["semantic_recovered"])

            logger.info(
                "[P1] %s | %s score=%.2f | matched=%s | missing=%s | recovered=%s",
                cand["name"], cname,
                p1["score"], p1["matched"], p1["missing"], p1["semantic_recovered"],
            )

        # ── Phase 2a: normalise coverage against pool max ─────────────────────
        phase1_map = {
            name: concept_results[cname][name]["phase1"]
            for name in concept_results[cname]
        }
        norm_map = normalize_pool(phase1_map)

        # ── Phase 2b: depth + comparative score ───────────────────────────────
        for cand in candidates:
            name = cand["name"]
            answer_text = concept_results[cname][name]["answer"]
            d = depth_score(q_text, answer_text, depth_scorer_model)
            cov_norm = norm_map.get(name, 0.0)
            comp = comparative_score(cov_norm, d, config)

            concept_results[cname][name]["depth_score"] = d
            concept_results[cname][name]["coverage_norm"] = cov_norm
            concept_results[cname][name]["comparative_score"] = comp

            logger.info(
                "[P2] %s | %s norm=%.2f | depth=%.2f | comparative=%.2f",
                name, cname, cov_norm, d, comp,
            )

    # ── Phase 3a: aggregate per-candidate scores ─────────────────────────────
    leaderboard: list[dict] = []

    for cand in candidates:
        name = cand["name"]
        comp_scores: list[float] = []
        depth_scores: list[float] = []
        per_question: dict[str, dict] = {}

        for concept in concepts:
            cname = concept["concept_name"]
            cr = concept_results.get(cname, {}).get(name)
            if not cr:
                continue
            p1 = cr["phase1"]
            comp_scores.append(cr["comparative_score"])
            depth_scores.append(cr["depth_score"])
            per_question[cname] = {
                "coverage_score": p1["score"],
                "matched_keywords": p1["matched"],
                "missing_keywords": p1["missing"],
                "semantic_recovered": p1["semantic_recovered"],
                "match_count": p1["match_count"],
                "coverage_norm": cr["coverage_norm"],
                "depth_score": round(cr["depth_score"], 4),
                "comparative_score": cr["comparative_score"],
                "is_external": concept.get("is_external", False),
            }

        f_score = final_candidate_score(comp_scores)
        avg_d = round(mean(depth_scores), 4) if depth_scores else 0.0
        reject, reason = rejection_logic(f_score, avg_d, config.segments.pass_threshold)
        seg = segmentation(f_score, config)

        if reject:
            logger.info("[REJECTED] %s reason=%s", name, reason)

        leaderboard.append({
            "name": name,
            "candidate_id": cand["candidate_id"],
            "application_id": cand["application_id"],
            "assessment_id": cand["assessment_id"],
            "final_score": f_score,
            "avg_depth": avg_d,
            "reject": reject,
            "reject_reason": reason,
            "segment": seg,
            "rank": None,
        })
        # Attach per_question separately so it can be serialised to JSON for the DB
        leaderboard[-1]["per_question"] = per_question

    # ── Phase 3b: sort + tiebreak ────────────────────────────────────────────
    leaderboard.sort(key=lambda x: x["final_score"], reverse=True)

    # Tiebreak between each consecutive pair within margin
    i = 0
    while i < len(leaderboard) - 1:
        a_entry = leaderboard[i]
        b_entry = leaderboard[i + 1]
        if abs(a_entry["final_score"] - b_entry["final_score"]) < config.thresholds.tiebreak_margin:
            # Use the first concept for tiebreak comparison
            first_concept = concepts[0]
            cname = first_concept["concept_name"]
            q_text = first_concept["question_text"]
            answer_a = concept_results.get(cname, {}).get(a_entry["name"], {}).get("answer", "")
            answer_b = concept_results.get(cname, {}).get(b_entry["name"], {}).get("answer", "")

            winner = _tiebreak(
                name_a=a_entry["name"],
                answer_a=answer_a,
                name_b=b_entry["name"],
                answer_b=answer_b,
                question_text=q_text,
                tiebreaker_model=tiebreaker_model,
                config=config,
            )
            logger.info(
                "[TIEBREAK] %s vs %s → winner=%s",
                a_entry["name"], b_entry["name"], winner,
            )
            if winner == b_entry["name"]:
                leaderboard[i], leaderboard[i + 1] = leaderboard[i + 1], leaderboard[i]
        i += 1

    # ── Phase 3c: assign ranks (passing candidates only) ─────────────────────
    rank = 1
    for entry in leaderboard:
        if not entry["reject"]:
            entry["rank"] = rank
            logger.info(
                "#%d %s %.3f %s",
                rank, entry["name"], entry["final_score"], entry["segment"],
            )
            rank += 1

    # ── Keyword gap report ────────────────────────────────────────────────────
    keyword_gap_report: dict[str, object] = {}
    for concept in concepts:
        cname = concept["concept_name"]
        req_kws = concept["required_keywords"]
        covered = covered_kws_by_concept.get(cname, set())
        gaps = [kw for kw in req_kws if kw not in covered]
        keyword_gap_report[cname] = gaps if gaps else "all covered"

    return leaderboard, keyword_gap_report

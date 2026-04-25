"""
XAI Service — Explainability for the Recruitment Pipeline
──────────────────────────────────────────────────────────
Provides three explainability layers:

  1. SHAP with AutoTokenizer masker
     build_shap_explainer() — creates a SHAP Explainer whose masker uses the
     model's actual WordPiece tokenizer, not a simple whitespace splitter.
     This is critical for BGE-reranker-v2-m3 (WordPiece subwords).

  2. Word-level SHAP aggregation
     aggregate_shap_to_words() — sums subword token attributions ("##ing", "##orch")
     back into whole-word attributions, then returns the top-K by magnitude.

  3. Human-readable HR explanation card + machine-readable JSON block
     generate_hr_explanation() — produces:
       a) A structured natural-language card for HR professionals (no ML jargon).
       b) A machine-readable JSON dict for LLM chatbots / API consumers.

Design notes:
  • SHAP computation is expensive — only run for SHAP_K top candidates (default 5).
  • All functions degrade gracefully: if shap/transformers not installed,
    generate_hr_explanation() produces a deterministic fallback card using the
    feature_attribution data already computed in ranking_graph.py.
  • The JSON block is self-contained: an LLM can answer "why was #1 ranked first?"
    using only the JSON block without needing any other pipeline context.

Usage:
    from services.xai_service import generate_hr_explanation, format_full_explanation

    human_card, machine_json = generate_hr_explanation(
        cv=cand,
        jd=jd_data,
        word_attributions=[],        # from aggregate_shap_to_words() or []
        ce_logit=cand["ce_logit"],   # raw CE logit
        rank=cand["rank_in_pool"],
        pool_size=cand["total_in_pool"],
    )
"""

from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass
from typing import Optional, Any

logger = logging.getLogger(__name__)

# ── CE model used for SHAP — must match embedding_service ────────────────────
_CE_MODEL_FOR_SHAP = "BAAI/bge-reranker-v2-m3"

# ── SHAP_K: only compute SHAP for top-K candidates ───────────────────────────
SHAP_K = 5


# ─────────────────────────────────────────────────────────────────────────────
# Data class
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WordAttribution:
    """Aggregated word-level SHAP attribution after subword merging."""
    word:             str
    shap_value:       float
    is_positive:      bool
    absolute_value:   float


# ─────────────────────────────────────────────────────────────────────────────
# SHAP setup — AutoTokenizer masker
# ─────────────────────────────────────────────────────────────────────────────

def build_shap_explainer(model_name: str = _CE_MODEL_FOR_SHAP):
    """
    Create a SHAP Explainer that uses the model's actual WordPiece tokenizer
    as the masker.

    WHY: shap.maskers.Text(r'\\W+') splits on whitespace, but bge-reranker-v2-m3
    uses WordPiece subwords ("PyTorch" → ["py", "##tor", "##ch"]).  The masker
    must match the model's tokenizer or SHAP values are unreliable — perturbations
    at the word level don't correspond to what the model actually processes.

    Returns:
        (explainer, tokenizer) tuple.
        Returns (None, None) if shap or transformers is not installed.
    """
    try:
        import shap                                               # type: ignore
        from transformers import AutoTokenizer                    # type: ignore
        from sentence_transformers import CrossEncoder            # type: ignore
    except ImportError as exc:
        logger.warning(
            "[xai_service] SHAP dependencies not available (%s). "
            "Install with: pip install shap transformers sentence-transformers",
            exc,
        )
        return None, None

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name)

        # Use the model's tokenizer as the masker — perturbations now match
        # exactly what the model tokenizes
        masker = shap.maskers.Text(tokenizer)

        cross_encoder = CrossEncoder(model_name, max_length=512)

        def predict_fn(texts):
            # texts is a list of masked/perturbed strings produced by SHAP
            # CrossEncoder.predict expects List[Tuple[str, str]] — SHAP passes
            # single strings; we wrap them as (text, "") for compatibility.
            pairs = [(t, "") for t in texts]
            scores = cross_encoder.predict(pairs)
            import numpy as np  # type: ignore
            return scores.reshape(-1, 1)

        explainer = shap.Explainer(predict_fn, masker)
        logger.info("[xai_service] SHAP explainer built with %s tokenizer.", model_name)
        return explainer, tokenizer

    except Exception as exc:
        logger.error("[xai_service] Failed to build SHAP explainer: %s", exc)
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Subword → word aggregation
# ─────────────────────────────────────────────────────────────────────────────

# Tokens to always discard from attribution output
_SPECIAL_TOKENS = frozenset({"[CLS]", "[SEP]", "[PAD]", "", "<s>", "</s>", "<pad>"})


def aggregate_shap_to_words(
    shap_values: Any,
    top_k: int = 10,
) -> list[WordAttribution]:
    """
    Merge WordPiece subword SHAP values into whole-word attributions.

    Problem: Raw SHAP on a WordPiece model produces entries like:
        ("py", +0.003), ("##tor", +0.001), ("##ch", +0.182)
    These are unreadable.  This function sums subword values into:
        WordAttribution(word="pytorch", shap_value=+0.186, is_positive=True)

    Algorithm:
      - A token starting with "##" is a continuation of the previous token.
      - Continuation tokens are merged into the current word by stripping "##"
        and summing their SHAP values.
      - Special tokens ([CLS], [SEP], etc.) are discarded.
      - Results are sorted by |shap_value| descending and truncated to top_k.

    Args:
        shap_values: A shap.Explanation object from explainer([text]).
        top_k:       Maximum word attributions to return.

    Returns:
        List of WordAttribution sorted by absolute SHAP value descending.
    """
    try:
        tokens = list(shap_values.data)
        values = list(shap_values.values)
    except AttributeError:
        logger.warning("[xai_service] Invalid shap_values object — returning [].")
        return []

    words: list[str]   = []
    word_vals: list[float] = []
    current_word = ""
    current_val  = 0.0

    for token, val in zip(tokens, values):
        val = float(val)
        if token.startswith("##"):
            # Continuation — append to current word
            current_word += token[2:]
            current_val  += val
        else:
            # New token — flush previous word
            if current_word and current_word not in _SPECIAL_TOKENS:
                words.append(current_word)
                word_vals.append(current_val)
            current_word = token
            current_val  = val

    # Flush the last word
    if current_word and current_word not in _SPECIAL_TOKENS:
        words.append(current_word)
        word_vals.append(current_val)

    # Sort by absolute value descending
    pairs = sorted(zip(words, word_vals), key=lambda x: -abs(x[1]))

    result = []
    for word, val in pairs[:top_k]:
        if word in _SPECIAL_TOKENS:
            continue
        result.append(WordAttribution(
            word=word,
            shap_value=round(val, 4),
            is_positive=val > 0,
            absolute_value=round(abs(val), 4),
        ))

    return result


def explain_candidate(
    jd_text: str,
    cv_text: str,
    explainer: Any,
    top_k: int = 10,
) -> list[WordAttribution]:
    """
    Run SHAP on a single (jd_text, cv_text) pair and return word-level attributions.
    Returns [] if explainer is None or SHAP fails.
    """
    if explainer is None:
        return []
    try:
        combined = f"{jd_text} [SEP] {cv_text}"
        shap_vals = explainer([combined])
        return aggregate_shap_to_words(shap_vals[0], top_k=top_k)
    except Exception as exc:
        logger.warning("[xai_service] SHAP explanation failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Math utility (mirrored from embedding_service to avoid circular import)
# ─────────────────────────────────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# ─────────────────────────────────────────────────────────────────────────────
# HR explanation generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_hr_explanation(
    cv: dict,
    jd: dict,
    word_attributions: list[WordAttribution],
    ce_logit: float,
    gt_label: Optional[str] = None,
    rank: int = 1,
    pool_size: int = 20,
) -> tuple[str, dict]:
    """
    Generate a dual-format explanation for HR and LLM consumers.

    Args:
        cv:                  Candidate dict from ranking pipeline (or ParsedCV.model_dump()).
        jd:                  JD dict from context_gatherer_node (or ParsedJD.model_dump()).
        word_attributions:   Output of aggregate_shap_to_words() or [] for fallback.
        ce_logit:            Raw cross-encoder logit (before sigmoid).
        gt_label:            Ground-truth label if available ("good_fit" etc.).
        rank:                1-indexed rank in pool.
        pool_size:           Total candidates in pool.

    Returns:
        (human_readable_card: str, machine_json: dict)

        human_readable_card — for UI display, email, or PDF report.
        machine_json        — for LLM chatbot context or REST API response.
    """
    # ── Derive match score from CE logit ──────────────────────────────────────
    match_score = round(_sigmoid(ce_logit) * 100)

    # Fall back to the pipeline's final_score if CE logit = 0 (model not run)
    if ce_logit == 0.0 and "final_score" in cv:
        match_score = round(float(cv["final_score"]))

    # ── Candidate identity ────────────────────────────────────────────────────
    candidate_name = (
        cv.get("candidate_name")
        or f"{cv.get('first_name', '')} {cv.get('last_name', '')}".strip()
        or "Unknown Candidate"
    )
    jd_title = jd.get("job_title") or jd.get("title") or "N/A"

    # ── Experience comparison ─────────────────────────────────────────────────
    cv_years  = int(cv.get("years_of_experience") or cv.get("fulltime_years") or 0)
    req_years = int(jd.get("required_years") or 0)
    if req_years == 0:
        exp_line = f"{cv_years} years experience (student/entry-level role)"
    elif cv_years >= req_years:
        exp_line = f"{cv_years} years vs {req_years} required — meets/exceeds threshold"
    else:
        exp_line = f"{cv_years} years vs {req_years} required — below threshold"

    # ── Map SHAP word attributions back to JD skills ─────────────────────────
    jd_skills_raw = jd.get("required_skills") or []
    if jd_skills_raw and isinstance(jd_skills_raw[0], dict):
        jd_skill_names = [s.get("name", "") for s in jd_skills_raw]
    else:
        jd_skill_names = [str(s) for s in jd_skills_raw]
    jd_skills_lower = {s.lower() for s in jd_skill_names}

    # CV skill names (handle both dict and string formats)
    cv_skills_raw = cv.get("skills") or []
    cv_skills_lower: set[str] = set()
    for s in cv_skills_raw:
        if isinstance(s, dict):
            cv_skills_lower.add((s.get("skill_name") or s.get("name") or "").lower())
        else:
            cv_skills_lower.add(str(s).lower())

    # Also include matched_skills from deterministic scoring
    for ms in cv.get("matched_skills") or []:
        if isinstance(ms, dict):
            cv_skills_lower.add((ms.get("skill") or "").lower())

    positive_words = {a.word.lower() for a in word_attributions if a.is_positive}
    negative_words = {a.word.lower() for a in word_attributions if not a.is_positive}

    # ── Build strengths list ──────────────────────────────────────────────────
    strengths: list[str] = []

    # 1. LLM-generated strengths (highest quality signal)
    for s in (cv.get("llm_scores") or {}).get("strengths") or []:
        if s and s != "Manual review recommended":
            strengths.append(s)

    # 2. Skills in CV that match JD requirements + have positive SHAP signal
    shap_matched = []
    for skill in jd_skill_names:
        skill_l = skill.lower()
        in_cv = skill_l in cv_skills_lower
        shap_pos = any(
            skill_l in tok or tok in skill_l
            for tok in positive_words
        ) if positive_words else in_cv
        if in_cv and shap_pos:
            shap_matched.append(skill)
    if shap_matched and not strengths:
        for skill in shap_matched[:3]:
            strengths.append(f"{skill} — directly matched JD requirement")

    # 3. Experience strength
    if cv_years >= req_years and req_years > 0:
        strengths.append(exp_line)

    # 4. GenAI / project evidence
    genai = cv.get("genai_data") or {}
    if genai.get("evidence"):
        terms = ", ".join(genai["evidence"][:3])
        context = genai.get("context", "unknown")
        strengths.append(f"GenAI evidence found ({terms}) — context: {context}")

    # ── Build gaps list ───────────────────────────────────────────────────────
    gaps: list[str] = []

    # 1. LLM-generated concerns
    for c in (cv.get("llm_scores") or {}).get("concerns") or []:
        if c and c != "LLM assessment unavailable":
            gaps.append(c)

    # 2. JD skills missing from CV or with negative SHAP signal
    missing = []
    for skill in jd_skill_names:
        skill_l = skill.lower()
        if skill_l not in cv_skills_lower:
            missing.append(skill)
        elif negative_words and any(
            skill_l in tok or tok in skill_l
            for tok in negative_words
        ):
            missing.append(f"{skill} (mentioned but weak evidence)")
    if missing and not gaps:
        gaps = missing[:5]

    # 3. Experience gap
    if cv_years < req_years and req_years > 0:
        gaps.append(exp_line)

    # ── Recommendation text ───────────────────────────────────────────────────
    if match_score >= 75:
        recommendation_text = (
            "Strong technical fit. Recommend advancing to technical interview."
        )
        action = "advance_to_interview"
    elif match_score >= 50:
        recommendation_text = (
            "Partial fit. Consider a screening call to assess gaps before committing."
        )
        action = "screening_call"
    else:
        recommendation_text = (
            "Weak fit for this role's requirements. Does not meet minimum threshold."
        )
        action = "decline"

    # Honour pool label if available
    pool_label = cv.get("recommendation") or ""
    if pool_label in ("Top Candidate", "Strong Runner-Up", "Strong Hire"):
        recommendation_text = (
            f"[{pool_label}] {recommendation_text}"
        )

    # ── Interview focus areas ─────────────────────────────────────────────────
    interview_focus_raw = (cv.get("llm_scores") or {}).get("interview_questions") or []
    if interview_focus_raw:
        interview_focus = interview_focus_raw[:3]
    elif gaps:
        interview_focus = [f"Probe depth on: {g.split(' (')[0]}" for g in gaps[:3]]
    else:
        interview_focus = ["System design and architecture", "Recent project deep-dive"]

    # ── Human-readable card ───────────────────────────────────────────────────
    SEP = "─" * 57

    def _bullet_block(items: list[str], icon: str, empty_msg: str) -> str:
        if not items:
            return f"  {empty_msg}"
        return "\n".join(f"  {icon} {item}" for item in items)

    strengths_block  = _bullet_block(strengths[:5], "✅", "(none detected from available data)")
    gaps_block       = _bullet_block(gaps[:5],      "⚠️ ", "(no major gaps identified)")
    interview_block  = _bullet_block(interview_focus, "•", "• general technical discussion")
    gt_line = f"\nGT Label:   {gt_label}" if gt_label else ""

    human_card = f"""CANDIDATE SUMMARY FOR HR
{SEP}
Candidate:  {candidate_name:<25} |  Rank: #{rank} of {pool_size}
Role:       {jd_title:<25} |  Match score: {match_score}/100
Experience: {exp_line}{gt_line}

WHY THIS CANDIDATE RANKED {"HIGH" if match_score >= 70 else "HERE"}
{strengths_block}

GAPS TO BE AWARE OF
{gaps_block}

RECOMMENDATION
{recommendation_text}

Focus interview on:
{interview_block}
{SEP}""".strip()

    # ── Machine-readable JSON block ───────────────────────────────────────────
    machine_json = {
        "candidate_id":    (
            cv.get("candidate_id") or cv.get("email") or candidate_name
        ),
        "candidate_name":  candidate_name,
        "jd_id":           (
            jd.get("requisition_id") or jd.get("jd_id") or jd_title
        ),
        "jd_title":        jd_title,
        "rank":            rank,
        "pool_size":       pool_size,
        "match_score":     match_score,
        "ce_logit":        round(ce_logit, 4),
        "ce_probability":  round(_sigmoid(ce_logit), 4),
        "pipeline_score":  cv.get("final_score"),
        "pool_label":      pool_label,
        "gt_label":        gt_label,
        "cv_years":        cv_years,
        "required_years":  req_years,
        "top_positive_signals": [a.word for a in word_attributions if a.is_positive][:5],
        "top_negative_signals": [a.word for a in word_attributions if not a.is_positive][:5],
        "matched_jd_skills": shap_matched[:8],
        "missing_jd_skills": [g.split(" (")[0] for g in gaps if g not in (
            (cv.get("llm_scores") or {}).get("concerns") or []
        )][:5],
        "strengths":       strengths[:5],
        "gaps":            gaps[:5],
        "recommendation":  action,
        "interview_focus": interview_focus,
        "genai_evidence":  (cv.get("genai_data") or {}).get("evidence") or [],
        "scoring_mode":    cv.get("scoring_mode"),
        "feature_attribution": [
            {"feature": a["feature"], "contribution": a["contribution_pts"]}
            for a in (cv.get("feature_attribution") or [])[:5]
        ],
    }

    return human_card, machine_json


def format_full_explanation(
    cv: dict,
    jd: dict,
    word_attributions: list[WordAttribution],
    ce_logit: float,
    gt_label: Optional[str] = None,
    rank: int = 1,
    pool_size: int = 20,
) -> str:
    """
    Render both the human-readable card and the JSON machine block as a single
    string.  This is the format suitable for:
      • Storing in semantic_analysis_reports.ai_insights
      • Passing as context to an LLM chatbot
      • Displaying in a collapsible UI panel
    """
    human, machine = generate_hr_explanation(
        cv=cv,
        jd=jd,
        word_attributions=word_attributions,
        ce_logit=ce_logit,
        gt_label=gt_label,
        rank=rank,
        pool_size=pool_size,
    )
    json_block = json.dumps(machine, indent=2, ensure_ascii=False)
    return f"{human}\n\n```json\n{json_block}\n```"


# ─────────────────────────────────────────────────────────────────────────────
# Batch runner for top-SHAP_K candidates
# ─────────────────────────────────────────────────────────────────────────────

def generate_explanations_for_pool(
    ranked_candidates: list[dict],
    jd: dict,
    explainer: Any = None,
    shap_k: int = SHAP_K,
) -> list[dict]:
    """
    Generate HR explanations for all ranked candidates.
    SHAP word attributions are computed only for the top shap_k candidates
    (expensive); the rest receive the deterministic fallback.

    Returns the ranked_candidates list with 'hr_explanation' and
    'hr_explanation_json' fields added (does not mutate original dicts).
    """
    jd_text   = jd.get("full_description") or jd.get("key_responsibilities") or ""
    result    = []

    for i, cand in enumerate(ranked_candidates):
        rank       = cand.get("rank_in_pool", i + 1)
        pool_size  = cand.get("total_in_pool", len(ranked_candidates))
        ce_logit   = float(cand.get("ce_logit", 0.0))

        # SHAP only for top shap_k
        attributions: list[WordAttribution] = []
        if explainer is not None and i < shap_k:
            cv_text = cand.get("raw_text") or ""
            attributions = explain_candidate(jd_text, cv_text, explainer, top_k=10)

        human, machine = generate_hr_explanation(
            cv=cand,
            jd=jd,
            word_attributions=attributions,
            ce_logit=ce_logit,
            rank=rank,
            pool_size=pool_size,
        )

        result.append({
            **cand,
            "hr_explanation":      human,
            "hr_explanation_json": machine,
            "shap_computed":       i < shap_k and explainer is not None,
        })

    return result

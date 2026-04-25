"""
Assessment Question Reviewer Service
An LLM Judge that validates generated questions against the Ground Truth
concept list before they are persisted. Rejected questions are sent back to
the generator with specific fix instructions (max 2 regeneration cycles).

Public API:
  review_questions(questions, job_title, retrieved_concepts)
      -> {"status": "approved"|"needs_revision",
          "approved_questions": [...],
          "improvements": [...]}

  regenerate_failed_questions(failed_questions, improvements, concepts_payload)
      -> list[dict]  # replacement questions, same structure as generator output
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from helpers.config import get_settings

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"
_MAX_REGENERATION_CYCLES = 2

_JUDGE_SYSTEM_PROMPT = (
    "You are a senior principal engineer acting as a strict quality gate for a technical assessment. "
    "Your job is to review AI-generated questions and reject any that fail the quality bar. "
    "You ONLY respond with valid JSON — no markdown, no prose, no explanation outside the JSON object. "
    "Be strict: a question that is vague, trivial, tests outside the Ground Truth, or has "
    "non-distinct MCQ options MUST be rejected."
)


# ─────────────────────────────────────────────────────────────────────────────
# LLM helper (sync — matches assessment_graph.py pattern)
# ─────────────────────────────────────────────────────────────────────────────

def _llm_call(messages: list[dict], max_tokens: int = 2000, max_retries: int = 3) -> Optional[str]:
    api_key = get_settings().OPENAI_API_KEY
    if not api_key:
        logger.warning("[reviewer] OPENAI_API_KEY not set.")
        return None

    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _OPENAI_MODEL,
                    "messages": messages,
                    "temperature": 0.1,   # low temp for consistent judging
                    "max_tokens": max_tokens,
                },
                timeout=90,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            if resp.status_code == 429:
                wait = min(30 * attempt, 90)
                logger.warning("[reviewer] Rate-limited — waiting %ds (attempt %d/%d).", wait, attempt, max_retries)
                time.sleep(wait)
                continue
            logger.error("[reviewer] HTTP %d on attempt %d: %s", resp.status_code, attempt, resp.text[:200])
        except httpx.TimeoutException:
            logger.warning("[reviewer] Timeout on attempt %d/%d.", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(10 * attempt)
        except Exception as exc:
            logger.error("[reviewer] Unexpected error on attempt %d: %s", attempt, exc)
            break
    return None


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}") + 1
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e])
            except json.JSONDecodeError:
                pass
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Judge prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_judge_prompt(
    questions: list[dict],
    job_title: str,
    retrieved_concepts: list[dict],
) -> list[dict]:
    ground_truth_lines = [
        f"- [{c['tool_name']} | {c['level']}] {c['concept_name']}: {c.get('notes') or '(no notes)'}"
        for c in retrieved_concepts
    ]
    ground_truth = "\n".join(ground_truth_lines)

    questions_json = json.dumps(questions, indent=2)

    user_msg = (
        f"You are reviewing {len(questions)} questions generated for a {job_title} technical assessment.\n\n"
        f"GROUND TRUTH (only topics allowed in questions):\n{ground_truth}\n\n"
        f"GENERATED QUESTIONS:\n{questions_json}\n\n"
        f"REJECTION CRITERIA — reject a question if ANY of the following apply:\n"
        f"1. HALLUCINATED TOPIC: The question tests a tool, technology, or concept NOT listed in the Ground Truth.\n"
        f"2. TRIVIAL QUESTION: The question is of 'What is X?' or 'Define X' style — too simplistic.\n"
        f"3. WEAK MCQ OPTIONS: MCQ options are not technically distinct or contain obvious distractors.\n"
        f"4. EMPTY KEYWORDS: required_keywords is empty, generic, or does not match what a correct "
        f"   answer must contain.\n"
        f"5. MISMATCHED METADATA: concept_name or tool_name does not match any Ground Truth entry.\n"
        f"6. DUPLICATE: The question is semantically identical to another question in the batch.\n\n"
        f"APPROVAL CRITERIA — approve only if ALL of the following hold:\n"
        f"  - Tests only concepts from the Ground Truth\n"
        f"  - Is scenario-based, debugging, or architectural (not definitional)\n"
        f"  - MCQ options are technically distinct and equally plausible at a glance\n"
        f"  - required_keywords precisely identifies what must appear in a correct answer\n"
        f"  - concept_name and tool_name exactly match a Ground Truth entry\n\n"
        f"RESPOND with valid JSON only — exactly this structure:\n"
        f"{{\n"
        f"  \"status\": \"approved\" | \"needs_revision\",\n"
        f"  \"approved_questions\": [ /* array of approved question objects (full original structure) */ ],\n"
        f"  \"improvements\": [\n"
        f"    {{\n"
        f"      \"question_index\": <0-based integer>,\n"
        f"      \"issue\": \"<concise description of what is wrong>\",\n"
        f"      \"suggested_fix\": \"<specific instruction for what the replacement question should do>\"\n"
        f"    }}\n"
        f"  ]  /* empty array [] if status == 'approved' */\n"
        f"}}"
    )

    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Regeneration prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_regeneration_prompt(
    failed_questions: list[dict],
    improvements: list[dict],
    concepts_payload: list[dict],
) -> list[dict]:
    ground_truth_lines = [
        f"- [{c['tool_name']} | {c['level']}] {c['concept_name']}: {c.get('notes') or '(no notes)'}"
        for c in concepts_payload
    ]
    ground_truth = "\n".join(ground_truth_lines)

    # Pair each failed question with its improvement feedback
    improvement_map = {imp["question_index"]: imp for imp in improvements}
    pairs = []
    for i, q in enumerate(failed_questions):
        imp = improvement_map.get(i, {})
        pairs.append(
            f"QUESTION {i + 1}:\n"
            f"  Original: {json.dumps(q)}\n"
            f"  Issue: {imp.get('issue', 'Quality bar not met')}\n"
            f"  Fix instruction: {imp.get('suggested_fix', 'Rewrite to meet quality bar')}"
        )
    questions_with_feedback = "\n\n".join(pairs)

    user_msg = (
        f"You must regenerate {len(failed_questions)} rejected assessment questions. "
        f"Each question was rejected for a specific reason listed below. "
        f"Follow the fix instruction exactly.\n\n"
        f"GROUND TRUTH (you MUST base replacements ONLY on these concepts):\n"
        f"{ground_truth}\n\n"
        f"REJECTED QUESTIONS WITH FIX INSTRUCTIONS:\n"
        f"{questions_with_feedback}\n\n"
        f"STRICT REQUIREMENTS for replacements:\n"
        f"1. Preserve the original question_type (mcq stays mcq, open_ended stays open_ended).\n"
        f"2. Test ONLY concepts from the Ground Truth — no hallucinated topics.\n"
        f"3. Scenario-based or architectural — PROHIBIT 'What is X?' or 'Define X' style.\n"
        f"4. For MCQ: correct_answer = letter only (A/B/C/D), options must be technically distinct.\n"
        f"   required_keywords = [correct_letter, plus 2-4 key concept terms from Ground Truth].\n"
        f"5. For open_ended: correct_answer = 3-5 sentence model answer. "
        f"   required_keywords = 3-6 key technical terms the answer must contain.\n"
        f"6. concept_name and tool_name MUST exactly match a Ground Truth entry.\n"
        f"7. No emojis. Plain technical English only.\n\n"
        f"Output MUST be a valid JSON array of exactly {len(failed_questions)} replacement objects. "
        f"Each object:\n"
        f"  - question_text, question_type, options, correct_answer, required_keywords, "
        f"concept_name, tool_name, ai_grading_guide\n\n"
        f"Respond with ONLY the JSON array. Begin immediately with '['."
    )

    return [
        {"role": "system", "content": (
            "You are a principal-level technical interviewer rewriting rejected assessment questions. "
            "You ONLY respond with a valid JSON array. Follow fix instructions precisely."
        )},
        {"role": "user", "content": user_msg},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def review_questions(
    questions: list[dict],
    job_title: str,
    retrieved_concepts: list[dict],
) -> dict:
    """
    Calls the LLM Judge with the generated questions batch.

    Returns:
    {
      "status": "approved" | "needs_revision",
      "approved_questions": [...],
      "improvements": [
        {
          "question_index": int,
          "issue": str,
          "suggested_fix": str
        }
      ]
    }

    On LLM failure, returns status="approved" with all questions passed through
    (fail-open so generation is never silently blocked).
    """
    if not questions:
        return {"status": "approved", "approved_questions": [], "improvements": []}

    messages = _build_judge_prompt(questions, job_title, retrieved_concepts)
    raw = _llm_call(messages, max_tokens=2000)

    if raw is None:
        logger.warning("[reviewer] LLM unavailable — passing all questions through (fail-open).")
        return {"status": "approved", "approved_questions": questions, "improvements": []}

    data = _parse_json(raw)
    if not data or "status" not in data:
        logger.warning("[reviewer] Could not parse judge response — passing all through. Raw: %s", raw[:300])
        return {"status": "approved", "approved_questions": questions, "improvements": []}

    status = str(data.get("status", "approved"))
    approved = data.get("approved_questions", [])
    improvements = data.get("improvements", [])

    if not isinstance(approved, list):
        approved = questions
    if not isinstance(improvements, list):
        improvements = []

    logger.info(
        "[reviewer] Judge verdict: %s — %d/%d approved, %d flagged.",
        status, len(approved), len(questions), len(improvements),
    )
    return {
        "status": status,
        "approved_questions": approved,
        "improvements": improvements,
    }


def regenerate_failed_questions(
    failed_questions: list[dict],
    improvements: list[dict],
    concepts_payload: list[dict],
) -> list[dict]:
    """
    Sends ONLY the rejected questions back to the Generator LLM with specific
    fix instructions. Returns regenerated replacements in the same structure.
    Max _MAX_REGENERATION_CYCLES to prevent infinite loops.

    On any failure, returns the original failed questions unchanged.
    """
    if not failed_questions:
        return []

    replacements = failed_questions  # default: keep originals if regeneration fails

    for cycle in range(1, _MAX_REGENERATION_CYCLES + 1):
        logger.info(
            "[reviewer] Regeneration cycle %d/%d for %d failed questions.",
            cycle, _MAX_REGENERATION_CYCLES, len(failed_questions),
        )
        messages = _build_regeneration_prompt(failed_questions, improvements, concepts_payload)
        raw = _llm_call(messages, max_tokens=3000)

        if raw is None:
            logger.warning("[reviewer] Regeneration LLM unavailable on cycle %d.", cycle)
            break

        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start != -1 and end > start:
                try:
                    parsed = json.loads(text[start:end])
                except json.JSONDecodeError:
                    logger.warning("[reviewer] Could not parse regeneration response on cycle %d.", cycle)
                    break
            else:
                logger.warning("[reviewer] No JSON array found in regeneration response on cycle %d.", cycle)
                break

        if isinstance(parsed, list) and len(parsed) > 0:
            replacements = parsed[:len(failed_questions)]
            logger.info(
                "[reviewer] Cycle %d produced %d replacement questions.", cycle, len(replacements)
            )
            break
        else:
            logger.warning("[reviewer] Empty or invalid list from regeneration on cycle %d.", cycle)
            break

    return replacements

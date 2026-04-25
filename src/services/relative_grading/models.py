"""
Real model wrappers — same method signatures as the original spec's mock_models.py.

Lemmatizer      → Python regex tokeniser (no spaCy needed in DB mode)
SemanticModel   → GPT-4o-mini batched semantic keyword recovery
DepthScorer     → GPT-4o-mini depth rating 0.0–1.0
TiebreakerModel → GPT-4o best-of-N answer comparison
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "it", "its", "was", "are", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "not", "this",
    "that", "these", "those", "as", "from", "which", "they", "their",
    "will", "would", "can", "could", "should", "may", "might", "also",
    "such", "when", "if", "then", "so", "we", "you", "i", "he", "she",
}


def _openai_call(
    model: str,
    messages: list[dict],
    max_tokens: int = 300,
    temperature: float = 0.0,
    retries: int = 3,
) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.error("[models] OPENAI_API_KEY not set.")
        return None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            if resp.status_code == 429:
                time.sleep(min(15 * attempt, 60))
                continue
            logger.warning("[models] HTTP %d on attempt %d.", resp.status_code, attempt)
        except httpx.TimeoutException:
            if attempt < retries:
                time.sleep(10 * attempt)
        except Exception as exc:
            logger.error("[models] Unexpected error on attempt %d: %s", attempt, exc)
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


class Lemmatizer:
    """Tokenises answer text into a set of lowercase non-stop-word tokens."""

    def lemmatize(self, text: str) -> set[str]:
        tokens = re.findall(r"\b[a-z][a-z0-9_]*\b", text.lower())
        return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


class SemanticModel:
    """
    GPT-4o-mini semantic keyword recovery.

    For a set of keywords not found via direct token match, sends a single
    batched prompt asking whether the answer semantically demonstrates
    understanding of each one.  Returns a dict[keyword → bool].
    """

    def batch_semantic_check(
        self,
        answer_text: str,
        missing_keywords: list[str],
    ) -> dict[str, bool]:
        if not missing_keywords:
            return {}

        kw_list = json.dumps(missing_keywords)
        user_msg = (
            f"You are a strict technical evaluator.\n\n"
            f"Candidate Answer (first 800 chars):\n{answer_text[:800]}\n\n"
            f"Missing Keywords: {kw_list}\n\n"
            f"For each keyword, decide whether the candidate's answer SEMANTICALLY "
            f"demonstrates understanding of that concept — exact wording is NOT required.\n\n"
            f"Reply ONLY with a JSON object where every key is a keyword and the value is "
            f"true or false.\nExample: {{\"async\": true, \"event loop\": false}}"
        )
        raw = _openai_call(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You evaluate technical answers. Reply with JSON only — no prose.",
                },
                {"role": "user", "content": user_msg},
            ],
            max_tokens=len(missing_keywords) * 30 + 50,
            temperature=0.0,
        )
        if not raw:
            return {kw: False for kw in missing_keywords}
        data = _parse_json(raw)
        return {kw: bool(data.get(kw, False)) for kw in missing_keywords}


class DepthScorer:
    """
    GPT-4o-mini depth scorer.

    Rates technical depth of an answer relative to a question on a 0.0–1.0
    scale.  Sigmoid-equivalent normalisation is done via the prompt framing.
    """

    def score(self, question_text: str, answer_text: str) -> float:
        if not answer_text.strip():
            return 0.0

        user_msg = (
            f"You are an expert technical interviewer scoring answer depth.\n\n"
            f"Question: {question_text}\n\n"
            f"Candidate Answer:\n{answer_text[:800]}\n\n"
            f"Rate the TECHNICAL DEPTH of this answer from 0.0 to 1.0:\n"
            f"  0.0  = Empty, off-topic, or completely wrong\n"
            f"  0.25 = Surface-level buzzwords, no real understanding\n"
            f"  0.50 = Partial understanding — correct but shallow\n"
            f"  0.75 = Good technical knowledge with practical insight\n"
            f"  1.0  = Exceptional — comprehensive, production-grade, no errors\n\n"
            f"Reply ONLY with a single float between 0.0 and 1.0. No explanation."
        )
        raw = _openai_call(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You score technical answer depth. Reply with a single float only.",
                },
                {"role": "user", "content": user_msg},
            ],
            max_tokens=10,
            temperature=0.0,
        )
        if not raw:
            return 0.5
        try:
            return max(0.0, min(1.0, float(raw.strip())))
        except ValueError:
            m = re.search(r"(\d+\.?\d*)", raw)
            if m:
                try:
                    return max(0.0, min(1.0, float(m.group(1))))
                except ValueError:
                    pass
            return 0.5


class TiebreakerModel:
    """
    GPT-4o best-of-N deterministic tiebreaker.

    Compares Answer A vs Answer B for the same question.
    Runs `runs` times at temperature=0 and returns the majority winner.
    """

    def compare(
        self,
        question_text: str,
        answer_a: str,
        answer_b: str,
        runs: int = 3,
        temperature: float = 0.0,
    ) -> str:
        votes: dict[str, int] = {"A": 0, "B": 0}
        for _ in range(runs):
            winner = self._single_run(question_text, answer_a, answer_b, temperature)
            votes[winner] = votes.get(winner, 0) + 1
        return "A" if votes.get("A", 0) >= votes.get("B", 0) else "B"

    def _single_run(
        self,
        question_text: str,
        answer_a: str,
        answer_b: str,
        temperature: float,
    ) -> str:
        user_msg = (
            f"Question: {question_text}\n\n"
            f"Answer A:\n{answer_a[:600]}\n\n"
            f"Answer B:\n{answer_b[:600]}\n\n"
            f"Which answer demonstrates deeper technical understanding? Reply: A or B"
        )
        raw = _openai_call(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a technical interviewer. Compare these two answers to the "
                        "same question and reply with ONLY the letter A or B for the better answer."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            max_tokens=5,
            temperature=temperature,
        )
        if not raw:
            return "A"
        text = raw.strip().upper()
        if "B" in text and "A" not in text:
            return "B"
        return "A"

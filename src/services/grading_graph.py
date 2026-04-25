"""
AI Grading LangGraph Workflow
Called synchronously from POST /api/v1/assessment/{id}/submit.

Graph nodes (sequential):
  1. load_submission_node    – loads CandidateAssessment + all template questions from DB;
                               enriches each question with required_keywords from
                               generated_assessment_questions (where template_question_id matches)
  2. ai_grader_node          – grades every answer:
                                 MCQ        → exact letter-match (no LLM needed)
                                 open_ended → keyword-grounded GPT-4o-mini prompt when
                                              required_keywords is present; falls back to
                                              model-answer grading when empty
  3. aggregate_scores_node   – sums scores, calculates score_pct, determines passed/failed,
                               persists AssessmentAnswer rows + updates CandidateAssessment
  4. generate_report_node    – GPT-4o-mini builds AssessmentReport (strengths, weaknesses,
                               overall_feedback, recommendation); upserts the DB row

Error handling:
  • Any node may set state["error"] to abort to the END node.
  • ai_grader_node never crashes — OpenAI failures produce score=0 +
    ai_feedback="Manual Review Required".
  • generate_report_node falls back to a deterministic report on LLM failure.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from database.connection import SessionLocal
from helpers.config import get_settings
from models.db.assessment_answer import AssessmentAnswer
from models.db.assessment_report import AssessmentReport
from models.db.assessment_template_question import AssessmentTemplateQuestion
from models.db.candidate_assessment import CandidateAssessment
from models.db.generated_assessment_question import GeneratedAssessmentQuestion

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

class GradingState(TypedDict):
    assessment_id: int
    submitted_answers: list[dict]    # [{"question_id": int, "candidate_answer": str}]

    # populated by load_submission_node
    template_id: Optional[int]
    passing_score: Optional[float]
    questions: list[dict]            # full question records from DB

    # populated by ai_grader_node
    graded_answers: list[dict]       # + score_awarded, ai_feedback per question

    # populated by aggregate_scores_node
    total_score: float
    total_possible: float
    score_pct: float
    passed: Optional[bool]

    # populated by generate_report_node
    report_id: Optional[int]
    strengths: str
    weaknesses: str
    ai_feedback: str
    recommendation: str

    error: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Shared LLM helper
# ─────────────────────────────────────────────────────────────────────────────

def _llm_call(messages: list[dict], max_tokens: int = 400, max_retries: int = 3) -> Optional[str]:
    api_key = get_settings().OPENAI_API_KEY
    if not api_key:
        return None
    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _OPENAI_MODEL,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                },
                timeout=45,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            if resp.status_code == 429:
                time.sleep(min(20 * attempt, 60))
                continue
            logger.warning("[grading_llm] HTTP %d on attempt %d.", resp.status_code, attempt)
        except httpx.TimeoutException:
            if attempt < max_retries:
                time.sleep(10 * attempt)
        except Exception as exc:
            logger.error("[grading_llm] Error on attempt %d: %s", attempt, exc)
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
# Node 1 — Load Submission
# ─────────────────────────────────────────────────────────────────────────────

def load_submission_node(state: GradingState) -> GradingState:
    """
    Loads CandidateAssessment + all template questions from DB.
    Validates that the assessment is in a gradeable state.
    """
    assessment_id = state["assessment_id"]
    db = SessionLocal()
    try:
        assessment: Optional[CandidateAssessment] = (
            db.query(CandidateAssessment)
            .filter(CandidateAssessment.assessment_id == assessment_id)
            .first()
        )
        if not assessment:
            return {**state, "error": f"Assessment {assessment_id} not found."}
        if assessment.status == "Submitted":
            return {**state, "error": "Assessment already submitted."}
        if not assessment.template_id:
            return {**state, "error": "Assessment has no questions configured."}

        questions_db = (
            db.query(AssessmentTemplateQuestion)
            .filter(AssessmentTemplateQuestion.template_id == assessment.template_id)
            .all()
        )
        if not questions_db:
            return {**state, "error": "No questions found in this assessment template."}

        # ── Enrich with required_keywords from generated_assessment_questions ──
        # Look up by template_question_id (the FK that links the two tables).
        # Works for both knowledge-grounded questions and "general LLM" fallbacks
        # (concept_id IS NULL) — required_keywords is always populated.
        question_ids = [q.question_id for q in questions_db]
        gq_by_tq_id: dict[int, GeneratedAssessmentQuestion] = {
            gq.template_question_id: gq
            for gq in db.query(GeneratedAssessmentQuestion)
            .filter(GeneratedAssessmentQuestion.template_question_id.in_(question_ids))
            .all()
            if gq.template_question_id is not None
        }

        questions = []
        for q in questions_db:
            model_answer = q.correct_answer or ""
            grading_guide = ""
            if model_answer.strip().startswith("{"):
                try:
                    stored = json.loads(model_answer)
                    grading_guide = stored.get("grading_guide", "")
                    model_answer = stored.get("answer", model_answer)
                except json.JSONDecodeError:
                    pass

            gq = gq_by_tq_id.get(q.question_id)
            required_keywords: list[str] = []
            if gq:
                kw = gq.required_keywords
                if isinstance(kw, list) and kw:
                    required_keywords = [str(k) for k in kw]
                elif not kw:
                    logger.warning(
                        "[load_submission] question_id=%d has a generated_assessment_question "
                        "but required_keywords is empty — will fall back to model-answer grading.",
                        q.question_id,
                    )

            questions.append({
                "question_id": q.question_id,
                "question_text": q.question_text,
                "question_type": (q.question_type or "mcq").lower(),
                "points": q.points or 10,
                "correct_answer": model_answer,
                "grading_guide": grading_guide,
                "options": json.loads(q.options) if q.options else None,
                "required_keywords": required_keywords,  # [] for legacy questions
                "concept_id": gq.concept_id if gq else None,  # None = general LLM fallback
            })

        passing_score = float(assessment.passing_score) if assessment.passing_score else None
        logger.info(
            "[load_submission] Assessment %d — %d questions loaded (%d with required_keywords).",
            assessment_id, len(questions),
            sum(1 for q in questions if q["required_keywords"]),
        )

        return {
            **state,
            "template_id": assessment.template_id,
            "passing_score": passing_score,
            "questions": questions,
        }
    except Exception as exc:
        logger.exception("[load_submission] Fatal error.")
        return {**state, "error": f"Failed to load assessment: {exc}"}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — AI Grader
# ─────────────────────────────────────────────────────────────────────────────

def _grade_mcq(candidate_answer: str, correct_answer: str, points: int) -> tuple[float, str]:
    """Full points for correct letter, zero for wrong. Never raises."""
    def _letter(s: str) -> str:
        s = s.strip().upper()
        return s[0] if s and s[0] in "ABCD" else s

    cand = _letter(candidate_answer)
    correct = _letter(correct_answer)
    if cand == correct:
        return float(points), "Correct answer."
    return 0.0, f"Incorrect. The correct answer is '{correct}'."


def _grade_open_ended(
    question_text: str,
    candidate_answer: str,
    correct_answer: str,
    grading_guide: str,
    points: int,
    required_keywords: list[str] | None = None,
) -> tuple[float, str]:
    """
    GPT-4o-mini grader for open-ended questions.

    Two grading modes depending on whether required_keywords is populated:

    Keyword-grounded mode (knowledge-based assessments):
      Uses required_keywords as the Ground Truth. The candidate must demonstrate
      semantic understanding of these concepts — verbatim mention is NOT required.
      Score reflects how many core concepts were correctly addressed.

    Model-answer mode (legacy / fallback):
      Uses correct_answer + grading_guide as the reference.
      Falls back to score=0 + "Manual Review Required" on LLM failure.
    """
    if not candidate_answer.strip():
        return 0.0, "No answer provided."

    use_keywords = bool(required_keywords)

    if use_keywords:
        keywords_str = ", ".join(f'"{kw}"' for kw in required_keywords)
        user_msg = (
            f"You are a strict but fair technical examiner grading a candidate's answer.\n\n"
            f"Question: {question_text}\n\n"
            f"Required Concepts (Ground Truth): [{keywords_str}]\n\n"
            f"Model Answer (for reference): {correct_answer or '(not provided)'}\n\n"
            f"Grading Criteria: {grading_guide or 'Full marks for a technically complete and accurate answer.'}\n\n"
            f"Candidate Answer: {candidate_answer}\n\n"
            f"GRADING INSTRUCTION:\n"
            f"Compare the candidate's answer against the Required Concepts above. "
            f"The candidate does NOT need to mention these concepts verbatim, but must "
            f"demonstrate a clear semantic understanding of each one. "
            f"Score based on how many of these core concepts were correctly and meaningfully addressed.\n\n"
            f"Scoring rules:\n"
            f"  - Full marks ({points}): Demonstrates clear understanding of ALL or nearly all "
            f"required concepts with accurate, production-grade reasoning.\n"
            f"  - Partial credit (1-{points - 1}): Covers SOME concepts correctly but misses "
            f"critical ones, or addresses them with significant inaccuracies.\n"
            f"  - Zero (0): Answer is wrong, vague, off-topic, or addresses none of the required concepts.\n\n"
            f"Respond ONLY with valid JSON:\n"
            f"{{\"score\": <integer 0-{points}>, "
            f"\"ai_feedback\": \"<2-3 sentences: which required concepts were demonstrated, "
            f"which were missing or incorrect, and one specific improvement the candidate should study>\"}}"
        )
    else:
        # Legacy / fallback: grade against model answer + grading guide
        user_msg = (
            f"You are a strict but fair technical examiner. Grade the candidate's answer using the "
            f"reference answer and grading guide below. Apply partial credit where justified.\n\n"
            f"Question: {question_text}\n\n"
            f"Reference Answer: {correct_answer}\n\n"
            f"Grading Guide: {grading_guide or 'Full marks for a technically complete and accurate answer.'}\n\n"
            f"Candidate Answer: {candidate_answer}\n\n"
            f"Scoring rules:\n"
            f"  - Full marks ({points}): Candidate demonstrates clear mastery — hits all key concepts, "
            f"shows practical understanding, no significant errors.\n"
            f"  - Partial credit (1-{points - 1}): Answer is partially correct — covers some key concepts "
            f"but misses critical points, has minor technical inaccuracies, or lacks depth.\n"
            f"  - Zero (0): Answer is wrong, off-topic, vague to the point of uselessness, or not provided.\n\n"
            f"Respond ONLY with valid JSON — no prose outside the JSON:\n"
            f"{{\"score\": <integer 0-{points}>, "
            f"\"ai_feedback\": \"<2-3 sentences: state what was correct, what was missing or wrong, "
            f"and one specific improvement the candidate should study>\"}}"
        )

    raw = _llm_call(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert technical interviewer. "
                    "Evaluate answers objectively and fairly. "
                    "Always respond with valid JSON only — no markdown, no extra text."
                ),
            },
            {"role": "user", "content": user_msg},
        ],
        max_tokens=300,
    )

    if raw is None:
        logger.warning("[ai_grader] LLM unavailable for open-ended question — marking for manual review.")
        return 0.0, "Manual Review Required — AI grading service unavailable."

    data = _parse_json(raw)
    if not data or "score" not in data:
        logger.warning("[ai_grader] Could not parse LLM response: %s", raw[:200])
        return 0.0, "Manual Review Required — AI response could not be parsed."

    score = max(0.0, min(float(points), float(data.get("score", 0))))
    feedback = str(data.get("ai_feedback", "No feedback provided."))
    return round(score, 2), feedback


def ai_grader_node(state: GradingState) -> GradingState:
    """
    Grades every answer:
      MCQ        → exact letter-match (no LLM needed)
      open_ended → keyword-grounded prompt when required_keywords is present;
                   falls back to model-answer prompt for legacy questions
    Never raises — errors produce score=0 + 'Manual Review Required'.
    """
    submitted_map = {a["question_id"]: a["candidate_answer"] for a in state["submitted_answers"]}
    graded: list[dict] = []

    for q in state["questions"]:
        qid = q["question_id"]
        candidate_answer = submitted_map.get(qid, "")
        required_keywords: list[str] = q.get("required_keywords") or []

        try:
            if not candidate_answer:
                score_awarded, ai_feedback = 0.0, "No answer provided."
            elif q["question_type"] == "mcq":
                score_awarded, ai_feedback = _grade_mcq(
                    candidate_answer=candidate_answer,
                    correct_answer=q["correct_answer"],
                    points=q["points"],
                )
            else:
                if not required_keywords:
                    logger.warning(
                        "[ai_grader] question_id=%d has no required_keywords — "
                        "falling back to model-answer grading.",
                        qid,
                    )
                score_awarded, ai_feedback = _grade_open_ended(
                    question_text=q["question_text"],
                    candidate_answer=candidate_answer,
                    correct_answer=q["correct_answer"],
                    grading_guide=q["grading_guide"],
                    points=q["points"],
                    required_keywords=required_keywords or None,
                )
        except Exception as exc:
            logger.warning("[ai_grader] Unexpected error grading question %d: %s", qid, exc)
            score_awarded, ai_feedback = 0.0, "Manual Review Required — grading error."

        graded.append({
            **q,
            "candidate_answer": candidate_answer,
            "score_awarded": score_awarded,
            "ai_feedback": ai_feedback,
        })
        logger.debug(
            "[ai_grader] Q%d (%s, keywords=%d): %.1f/%d — %s",
            qid, q["question_type"], len(required_keywords),
            score_awarded, q["points"], ai_feedback[:60],
        )

    logger.info("[ai_grader] Graded %d questions for assessment %d.", len(graded), state["assessment_id"])
    return {**state, "graded_answers": graded}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — Aggregate Scores
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_scores_node(state: GradingState) -> GradingState:
    """
    Sums scores, computes score_pct, determines passed/failed.
    Persists AssessmentAnswer rows + updates CandidateAssessment status/score.
    """
    assessment_id = state["assessment_id"]
    graded = state["graded_answers"]

    total_score = sum(g["score_awarded"] for g in graded)
    total_possible = sum(g["points"] for g in graded)
    score_pct = round((total_score / total_possible * 100) if total_possible > 0 else 0.0, 2)

    passing_score = state["passing_score"]
    if passing_score is None:
        passing_score = round(total_possible * 0.6, 2)  # default 60%
    passed = total_score >= passing_score

    db = SessionLocal()
    try:
        # Wipe previous answers (idempotency on retry)
        db.query(AssessmentAnswer).filter(
            AssessmentAnswer.assessment_id == assessment_id
        ).delete(synchronize_session=False)
        db.flush()

        for g in graded:
            db.add(AssessmentAnswer(
                assessment_id=assessment_id,
                question_id=g["question_id"],
                candidate_answer=g["candidate_answer"],
                score_awarded=g["score_awarded"],
                ai_feedback=g["ai_feedback"],
            ))

        assessment: Optional[CandidateAssessment] = (
            db.query(CandidateAssessment)
            .filter(CandidateAssessment.assessment_id == assessment_id)
            .first()
        )
        if assessment:
            assessment.total_score = total_score
            assessment.passed = passed
            assessment.status = "Submitted"
            assessment.submitted_at = datetime.utcnow()

        db.commit()
        logger.info(
            "[aggregate_scores] Assessment %d — total=%.1f/%.0f (%.1f%%), passed=%s.",
            assessment_id, total_score, total_possible, score_pct, passed,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("[aggregate_scores] DB error for assessment %d.", assessment_id)
        return {**state, "error": f"Score aggregation failed: {exc}"}
    finally:
        db.close()

    return {
        **state,
        "total_score": total_score,
        "total_possible": total_possible,
        "score_pct": score_pct,
        "passing_score": passing_score,
        "passed": passed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — Generate Report
# ─────────────────────────────────────────────────────────────────────────────

def generate_report_node(state: GradingState) -> GradingState:
    """
    GPT-4o-mini generates AssessmentReport (strengths, weaknesses,
    overall_feedback, recommendation) based on all graded answers.
    Falls back to a deterministic report if the LLM is unavailable.
    Upserts the assessment_reports row. rank_in_pool is left NULL here
    and filled in by the recalculate_assessment_rankings Celery task.
    """
    assessment_id = state["assessment_id"]
    total_score = state["total_score"]
    total_possible = state["total_possible"]
    score_pct = state["score_pct"]
    graded = state["graded_answers"]

    # Build concise answer summary for the LLM
    summaries = []
    for g in graded:
        summaries.append(
            f"Q ({g['question_type']}): {g['question_text']}\n"
            f"Answer: {g['candidate_answer'] or '(none)'}\n"
            f"Score: {g['score_awarded']}/{g['points']} — {g['ai_feedback']}"
        )
    answers_text = "\n\n".join(summaries)[:3500]

    strengths, weaknesses, overall_feedback, recommendation = _llm_generate_report(
        answers_text=answers_text,
        total_score=total_score,
        total_possible=total_possible,
        score_pct=score_pct,
    )

    db = SessionLocal()
    try:
        existing = (
            db.query(AssessmentReport)
            .filter(AssessmentReport.assessment_id == assessment_id)
            .first()
        )
        if existing:
            existing.overall_score = total_score
            existing.ai_feedback = overall_feedback
            existing.strengths = strengths
            existing.weaknesses = weaknesses
            existing.recommendation = recommendation
            report_id = existing.report_id
        else:
            report = AssessmentReport(
                assessment_id=assessment_id,
                overall_score=total_score,
                ai_feedback=overall_feedback,
                strengths=strengths,
                weaknesses=weaknesses,
                recommendation=recommendation,
            )
            db.add(report)
            db.flush()
            report_id = report.report_id

        db.commit()
        logger.info("[generate_report] Report %d saved for assessment %d.", report_id, assessment_id)

    except Exception as exc:
        db.rollback()
        logger.exception("[generate_report] DB error for assessment %d.", assessment_id)
        return {**state, "error": f"Report generation failed: {exc}"}
    finally:
        db.close()

    return {
        **state,
        "report_id": report_id,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "ai_feedback": overall_feedback,
        "recommendation": recommendation,
    }


def _llm_generate_report(
    answers_text: str,
    total_score: float,
    total_possible: float,
    score_pct: float,
) -> tuple[str, str, str, str]:
    """Returns (strengths, weaknesses, overall_feedback, recommendation). Never raises."""
    user_msg = (
        f"You are a senior engineering hiring manager writing a definitive assessment report "
        f"that will be used in a hiring decision.\n\n"
        f"Overall Score: {total_score:.1f} / {total_possible:.0f} ({score_pct:.1f}%)\n\n"
        f"Detailed Results:\n{answers_text}\n\n"
        f"Instructions:\n"
        f"  - strengths: 2-4 bullet points naming SPECIFIC technical competencies the candidate "
        f"demonstrated with evidence from their answers. No generic praise.\n"
        f"  - weaknesses: 2-4 bullet points identifying SPECIFIC technical gaps or errors. "
        f"Reference concrete mistakes from the answers. No vague statements.\n"
        f"  - overall_feedback: 3-4 sentences — synthesize the candidate's overall technical "
        f"profile, signal-to-noise ratio in their answers, and suitability for a production "
        f"engineering environment. Be direct and evidence-based.\n"
        f"  - recommendation: EXACTLY one of: \"Hire\", \"Consider\", or \"Reject\".\n"
        f"    Use 'Hire' if score >= 75% AND demonstrated depth.\n"
        f"    Use 'Consider' if score 50-74% OR mixed quality answers.\n"
        f"    Use 'Reject' if score < 50% OR critical conceptual failures.\n\n"
        f"Respond ONLY with valid JSON:\n"
        f"{{\n"
        f"  \"strengths\": \"<bullet-point list>\",\n"
        f"  \"weaknesses\": \"<bullet-point list>\",\n"
        f"  \"overall_feedback\": \"<3-4 sentences>\",\n"
        f"  \"recommendation\": \"Hire\" | \"Consider\" | \"Reject\"\n"
        f"}}"
    )
    raw = _llm_call(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior engineering hiring manager writing binding assessment reports. "
                    "Be specific, evidence-based, and ruthlessly objective. "
                    "Respond with valid JSON only — no commentary outside the JSON object."
                ),
            },
            {"role": "user", "content": user_msg},
        ],
        max_tokens=900,
    )

    if raw:
        data = _parse_json(raw)
        if data:
            return (
                str(data.get("strengths", "")),
                str(data.get("weaknesses", "")),
                str(data.get("overall_feedback", "")),
                str(data.get("recommendation", "Consider")),
            )

    # Deterministic fallback
    rec = "Hire" if score_pct >= 70 else ("Consider" if score_pct >= 50 else "Reject")
    return (
        f"• Completed the assessment ({score_pct:.1f}% score).",
        "• Manual review recommended — AI report generation unavailable.",
        f"Candidate scored {total_score:.1f}/{total_possible:.0f} ({score_pct:.1f}%). Manual review recommended.",
        rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder + entry point
# ─────────────────────────────────────────────────────────────────────────────

def _route_after_load(state: GradingState) -> str:
    return "abort" if state.get("error") else "grade"

def _route_after_grade(state: GradingState) -> str:
    return "abort" if state.get("error") else "aggregate"

def _route_after_aggregate(state: GradingState) -> str:
    return "abort" if state.get("error") else "report"


def _build_grading_graph():
    graph = StateGraph(GradingState)

    graph.add_node("load_submission", load_submission_node)
    graph.add_node("ai_grader",       ai_grader_node)
    graph.add_node("aggregate_scores", aggregate_scores_node)
    graph.add_node("generate_report", generate_report_node)

    graph.set_entry_point("load_submission")

    graph.add_conditional_edges("load_submission",  _route_after_load,      {"grade": "ai_grader",       "abort": END})
    graph.add_conditional_edges("ai_grader",        _route_after_grade,     {"aggregate": "aggregate_scores", "abort": END})
    graph.add_conditional_edges("aggregate_scores", _route_after_aggregate, {"report": "generate_report", "abort": END})
    graph.add_edge("generate_report", END)

    return graph.compile()


_compiled_grading_graph = None


def run_grading_graph(assessment_id: int, submitted_answers: list[dict]) -> GradingState:
    """
    Entry point — called synchronously from the submission API endpoint.

    submitted_answers: [{"question_id": int, "candidate_answer": str}, ...]
    Returns the final GradingState (check state["error"] for failures).
    """
    global _compiled_grading_graph
    if _compiled_grading_graph is None:
        _compiled_grading_graph = _build_grading_graph()

    initial: GradingState = {
        "assessment_id": assessment_id,
        "submitted_answers": submitted_answers,
        "template_id": None,
        "passing_score": None,
        "questions": [],
        "graded_answers": [],
        "total_score": 0.0,
        "total_possible": 0.0,
        "score_pct": 0.0,
        "passed": None,
        "report_id": None,
        "strengths": "",
        "weaknesses": "",
        "ai_feedback": "",
        "recommendation": "",
        "error": None,
    }

    logger.info("[grading_graph] Starting grading for assessment %d.", assessment_id)
    result: GradingState = _compiled_grading_graph.invoke(initial)
    logger.info(
        "[grading_graph] Done — assessment %d, score=%.1f/%.0f (%.1f%%), passed=%s, error=%s",
        assessment_id,
        result.get("total_score", 0),
        result.get("total_possible", 0),
        result.get("score_pct", 0),
        result.get("passed"),
        result.get("error"),
    )
    return result

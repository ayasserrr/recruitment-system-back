"""
Technical Assessment LangGraph Workflow
═══════════════════════════════════════
Triggered automatically after CV Ranking is finalized (dispatched from send_shortlist_emails).

Graph nodes (sequential):
  1. load_context_node       – load TechnicalAssessmentConfig, required skills, seniority,
                               and all Shortlisted applications from DB
  2. generate_questions_node – GPT-4o-mini generates exactly 15 interdisciplinary questions
                               upserts AssessmentTemplate + saves AssessmentTemplateQuestion rows
  3. create_assessments_node – creates one CandidateAssessment row per Shortlisted application
  4. send_invitations_node   – sends unique HMAC-signed assessment invitation emails

Re-run safety:
  • generate_questions_node skips if the linked template already has ≥ _TOTAL_QUESTIONS (15) questions.
  • create_assessments_node skips individual applications that already have a CandidateAssessment.
  • send_invitations_node only emails candidates whose CandidateAssessment.status is still
    "Pending" (prevents double-send on Celery retry).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import joinedload

from database.connection import SessionLocal
from helpers.config import get_settings
from models.db.application import Application
from models.db.assessment_template import AssessmentTemplate
from models.db.assessment_template_question import AssessmentTemplateQuestion
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.requisition_required_skill import RequisitionRequiredSkill
from models.db.technical_assessment_config import TechnicalAssessmentConfig

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"
_TOTAL_QUESTIONS = 15          # exactly 15 interdisciplinary questions per assessment


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

class AssessmentState(TypedDict):
    requisition_id: int
    company_id: int
    job_title: str
    seniority: str
    config_id: int
    template_id: Optional[int]
    skills: list[dict]       # [{"skill_name": str, "skill_type": str}]
    shortlisted: list[dict]  # [{"application_id", "candidate_id", "email", "first_name"}]
    questions_generated: bool
    assessment_ids: list[int]
    error: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _llm_call(messages: list[dict], max_retries: int = 3, timeout: int = 90) -> Optional[str]:
    api_key = get_settings().OPENAI_API_KEY
    if not api_key:
        logger.warning("[assessment_llm] OPENAI_API_KEY not set — skipping LLM call.")
        return None

    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _OPENAI_MODEL,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 4500,
                },
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            if resp.status_code == 429:
                wait = min(30 * attempt, 90)
                logger.warning("[assessment_llm] Rate-limited — waiting %ds (attempt %d/%d).", wait, attempt, max_retries)
                time.sleep(wait)
                continue
            logger.error("[assessment_llm] HTTP %d on attempt %d: %s", resp.status_code, attempt, resp.text[:300])
        except httpx.TimeoutException:
            logger.warning("[assessment_llm] Timeout on attempt %d/%d.", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(10 * attempt)
        except Exception as exc:
            logger.error("[assessment_llm] Unexpected error on attempt %d: %s", attempt, exc)
            break
    return None


def _parse_json_response(raw: str) -> tuple[list | dict, bool]:
    """Strip markdown fences and parse JSON. Returns (data, success)."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        start = text.find("[") if "[" in text else text.find("{")
        end_char = "]" if text.find("[") != -1 and (text.find("{") == -1 or text.find("[") < text.find("{")) else "}"
        end = text.rfind(end_char) + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end]), True
            except json.JSONDecodeError:
                pass
    return [], False


def generate_assessment_token(assessment_id: int) -> str:
    """Generate an HMAC-SHA256 signed token for the assessment link."""
    secret = os.getenv("SECRET_KEY", "assessment-default-secret-key")
    msg = f"assessment:{assessment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_assessment_token(assessment_id: int, token: str) -> bool:
    """Verify the HMAC token for an assessment link."""
    expected = generate_assessment_token(assessment_id)
    return hmac.compare_digest(expected, token)


def build_assessment_url(assessment_id: int) -> str:
    cfg = get_settings()
    token = generate_assessment_token(assessment_id)
    return f"{cfg.APP_BASE_URL}/assessment/{assessment_id}?token={token}"


def _build_question_prompt(skills: list[str], seniority: str, job_title: str) -> list[dict]:
    skills_list = ", ".join(skills)
    system_msg = (
        "You are a principal-level technical interviewer writing a high-stakes screening assessment. "
        "You ONLY respond with a valid JSON array — no markdown, no prose, no explanation, no emojis."
    )
    user_msg = (
        f"Design a rigorous technical assessment for a {seniority}-level {job_title} role.\n\n"
        f"Required skills: {skills_list}\n\n"
        f"STRICT REQUIREMENTS:\n"
        f"1. Generate EXACTLY 15 questions — no more, no less.\n"
        f"2. Questions MUST synthesize multiple skills from the list above. Never isolate a single skill. "
        f"   Examples: '{skills[0]} + {skills[min(1, len(skills)-1)]} trade-offs', "
        f"   'debugging a failure in a system using {skills[min(2, len(skills)-1)]}', "
        f"   'architectural decision between two approaches involving multiple listed skills'.\n"
        f"3. Question types: include exactly 9 MCQ and 6 open_ended questions.\n"
        f"4. Complexity: focus on scenario-based problems, production debugging, architectural trade-offs, "
        f"   and design decisions. PROHIBIT 'What is X?' or 'Define X' style questions.\n"
        f"5. No emojis. No decorative symbols. Use plain, precise, technical English only.\n"
        f"6. Keep correct_answer for MCQ to the letter only (A, B, C, or D).\n"
        f"7. For open_ended, correct_answer must be a concise model answer (3-5 sentences max).\n"
        f"8. ai_grading_guide must be 1-3 sentences: list the key technical concepts required and "
        f"   what constitutes a partial vs full answer. No padding.\n"
        f"9. Each MCQ option must be a technically distinct, plausible answer — avoid obvious distractors.\n\n"
        f"Output MUST be a valid JSON array of exactly 15 objects. Each object:\n"
        f"  - question_text: string\n"
        f"  - question_type: \"mcq\" or \"open_ended\"\n"
        f"  - options: [\"A. ...\", \"B. ...\", \"C. ...\", \"D. ...\"] for mcq, null for open_ended\n"
        f"  - correct_answer: string (letter only for mcq; concise model answer for open_ended)\n"
        f"  - ai_grading_guide: string (concise scoring criteria)\n\n"
        f"Respond with ONLY the JSON array. Begin immediately with '['."
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — Load Context
# ─────────────────────────────────────────────────────────────────────────────

def load_context_node(state: AssessmentState) -> AssessmentState:
    """
    Loads from DB:
      - JobRequisition (seniority, job_title, company_id)
      - TechnicalAssessmentConfig (config_id, template_id)
      - RequisitionRequiredSkill (skills list)
      - All Shortlisted Applications with candidate emails
    """
    req_id = state["requisition_id"]
    logger.info("[assessment_context] Loading context for requisition %d.", req_id)

    db = SessionLocal()
    try:
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == req_id)
            .first()
        )
        if not jr:
            return {**state, "error": f"JobRequisition {req_id} not found."}

        config: Optional[TechnicalAssessmentConfig] = (
            db.query(TechnicalAssessmentConfig)
            .filter(TechnicalAssessmentConfig.requisition_id == req_id)
            .first()
        )
        if not config:
            logger.info(
                "[assessment_context] No TechnicalAssessmentConfig for requisition %d — skipping.", req_id
            )
            return {**state, "error": f"No TechnicalAssessmentConfig for requisition {req_id}."}

        skills = (
            db.query(RequisitionRequiredSkill)
            .filter(RequisitionRequiredSkill.requisition_id == req_id)
            .all()
        )

        # Fetch Shortlisted applications with candidate info
        posting: Optional[JobPosting] = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == req_id)
            .first()
        )
        shortlisted: list[dict] = []
        if posting:
            apps = (
                db.query(Application)
                .filter(
                    Application.posting_id == posting.posting_id,
                    Application.status == "Shortlisted",
                )
                .options(joinedload(Application.candidate))
                .all()
            )
            for app in apps:
                cand: Optional[Candidate] = app.candidate
                shortlisted.append({
                    "application_id": app.application_id,
                    "candidate_id": cand.candidate_id if cand else None,
                    "email": cand.email if cand else "",
                    "first_name": cand.first_name if cand else "Candidate",
                })

        logger.info(
            "[assessment_context] Requisition %d — %d skills, %d shortlisted candidates.",
            req_id, len(skills), len(shortlisted),
        )

        return {
            **state,
            "company_id": jr.company_id,
            "job_title": jr.job_title,
            "seniority": jr.seniority_level or "Mid-level",
            "config_id": config.config_id,
            "template_id": config.template_id,
            "skills": [
                {"skill_name": s.skill_name, "skill_type": s.skill_type or "required"}
                for s in skills
            ],
            "shortlisted": shortlisted,
        }

    except Exception as exc:
        logger.exception("[assessment_context] Fatal error.")
        return {**state, "error": f"Context load failed: {exc}"}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — Generate Questions
# ─────────────────────────────────────────────────────────────────────────────

def generate_questions_node(state: AssessmentState) -> AssessmentState:
    """
    Makes ONE GPT-4o-mini call with all required skills to generate exactly
    15 interdisciplinary questions. Questions cross-link multiple skills and
    focus on scenario-based, architectural, and debugging problems.

    Skip logic: template already has >= _TOTAL_QUESTIONS questions.
    """
    req_id = state["requisition_id"]
    config_id = state["config_id"]
    existing_template_id = state["template_id"]
    skills = state["skills"]
    seniority = state["seniority"]
    company_id = state["company_id"]
    job_title = state["job_title"]

    if not skills:
        logger.info("[assessment_gen] No skills defined for requisition %d — skipping.", req_id)
        return {**state, "questions_generated": False}

    skill_names = [s["skill_name"] for s in skills]

    db = SessionLocal()
    try:
        # Skip if existing template already has enough questions
        if existing_template_id:
            existing_count = (
                db.query(AssessmentTemplateQuestion)
                .filter(AssessmentTemplateQuestion.template_id == existing_template_id)
                .count()
            )
            if existing_count >= _TOTAL_QUESTIONS:
                logger.info(
                    "[assessment_gen] Template %d already has %d questions — skipping.",
                    existing_template_id, existing_count,
                )
                return {**state, "questions_generated": False, "template_id": existing_template_id}

        # Create or reuse template
        if not existing_template_id:
            template = AssessmentTemplate(
                company_id=company_id,
                title=f"Technical Assessment — {job_title}",
                description=f"AI-generated technical assessment for {job_title} ({seniority} level)",
                category="Technical",
            )
            db.add(template)
            db.flush()
            template_id = template.template_id

            config = db.query(TechnicalAssessmentConfig).filter(
                TechnicalAssessmentConfig.config_id == config_id
            ).first()
            if config:
                config.template_id = template_id
        else:
            template_id = existing_template_id
            db.query(AssessmentTemplateQuestion).filter(
                AssessmentTemplateQuestion.template_id == template_id
            ).delete(synchronize_session=False)

        logger.info(
            "[assessment_gen] Generating %d interdisciplinary questions for requisition %d "
            "across skills: %s",
            _TOTAL_QUESTIONS, req_id, ", ".join(skill_names),
        )

        messages = _build_question_prompt(skill_names, seniority, job_title)
        raw_response = _llm_call(messages)

        if raw_response is None:
            logger.warning("[assessment_gen] LLM returned None — saving fallback questions.")
            _save_fallback_questions(db, template_id, skill_names[0], seniority)
            db.commit()
            return {**state, "questions_generated": True, "template_id": template_id}

        parsed, ok = _parse_json_response(raw_response)
        if not ok or not isinstance(parsed, list) or len(parsed) == 0:
            logger.warning(
                "[assessment_gen] Invalid JSON from LLM: %s — using fallback.", raw_response[:300]
            )
            _save_fallback_questions(db, template_id, skill_names[0], seniority)
            db.commit()
            return {**state, "questions_generated": True, "template_id": template_id}

        total_saved = 0
        for q in parsed[:_TOTAL_QUESTIONS]:
            try:
                q_type = str(q.get("question_type", "mcq")).lower()
                options_raw = q.get("options")
                options_json = json.dumps(options_raw) if options_raw and isinstance(options_raw, list) else None
                correct_answer = q.get("correct_answer") or ""
                ai_grading_guide = q.get("ai_grading_guide") or ""

                if q_type == "open_ended" and ai_grading_guide:
                    correct_answer = json.dumps({
                        "answer": correct_answer,
                        "grading_guide": ai_grading_guide,
                    })

                db.add(AssessmentTemplateQuestion(
                    template_id=template_id,
                    question_text=str(q.get("question_text", "")),
                    question_type=q_type,
                    points=10,
                    correct_answer=correct_answer,
                    options=options_json,
                ))
                total_saved += 1
            except Exception as exc:
                logger.warning("[assessment_gen] Skipping malformed question: %s", exc)

        db.commit()
        logger.info(
            "[assessment_gen] Saved %d/%d questions for template %d (requisition %d).",
            total_saved, _TOTAL_QUESTIONS, template_id, req_id,
        )
        return {**state, "questions_generated": True, "template_id": template_id}

    except Exception as exc:
        db.rollback()
        logger.exception("[assessment_gen] DB error during question generation.")
        return {**state, "error": f"Question generation failed: {exc}"}
    finally:
        db.close()


def _save_fallback_questions(db, template_id: int, skill_name: str, seniority: str) -> None:
    """Save basic fallback questions when the LLM is unavailable."""
    fallback = [
        {
            "question_text": f"Explain the core concept of {skill_name} and its primary use case.",
            "question_type": "open_ended",
            "points": 10,
            "correct_answer": json.dumps({
                "answer": f"A comprehensive explanation of {skill_name} and its applications.",
                "grading_guide": (
                    f"Candidate should demonstrate understanding of {skill_name} fundamentals. "
                    f"Award full marks for clear explanation with practical examples."
                ),
            }),
            "options": None,
        },
        {
            "question_text": f"Which of the following BEST describes {skill_name} at the {seniority} level?",
            "question_type": "mcq",
            "points": 10,
            "correct_answer": "A",
            "options": json.dumps([
                f"A. Advanced proficiency with real-world {skill_name} deployment experience",
                f"B. Basic familiarity with {skill_name} concepts only",
                f"C. Theoretical knowledge of {skill_name} without practical application",
                f"D. No experience with {skill_name}",
            ]),
        },
    ]
    for f in fallback:
        db.add(AssessmentTemplateQuestion(
            template_id=template_id,
            question_text=f["question_text"],
            question_type=f["question_type"],
            points=f["points"],
            correct_answer=f["correct_answer"],
            options=f.get("options"),
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — Create Candidate Assessments
# ─────────────────────────────────────────────────────────────────────────────

def create_assessments_node(state: AssessmentState) -> AssessmentState:
    """
    For each Shortlisted application that does not yet have a CandidateAssessment,
    creates one row with status='Pending'.

    Idempotent: skips applications that already have a CandidateAssessment.
    """
    shortlisted = state["shortlisted"]
    config_id = state["config_id"]
    template_id = state["template_id"]

    if not shortlisted:
        logger.info("[assessment_create] No shortlisted candidates for requisition %d.", state["requisition_id"])
        return {**state, "assessment_ids": []}

    if not template_id:
        logger.warning(
            "[assessment_create] No template_id available for requisition %d — cannot create assessments.",
            state["requisition_id"],
        )
        return {**state, "error": "No assessment template available.", "assessment_ids": []}

    db = SessionLocal()
    created_ids: list[int] = []
    try:
        for cand in shortlisted:
            app_id = cand["application_id"]

            # Skip if already exists
            existing = (
                db.query(CandidateAssessment)
                .filter(CandidateAssessment.application_id == app_id)
                .first()
            )
            if existing:
                created_ids.append(existing.assessment_id)
                continue

            assessment = CandidateAssessment(
                application_id=app_id,
                config_id=config_id,
                template_id=template_id,
                status="Pending",
                attempt_number=1,
            )
            db.add(assessment)
            db.flush()
            created_ids.append(assessment.assessment_id)

        db.commit()
        logger.info(
            "[assessment_create] Created/found %d CandidateAssessment rows for requisition %d.",
            len(created_ids), state["requisition_id"],
        )
        return {**state, "assessment_ids": created_ids}

    except Exception as exc:
        db.rollback()
        logger.exception("[assessment_create] DB error.")
        return {**state, "error": f"Assessment creation failed: {exc}", "assessment_ids": []}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — Send Invitations
# ─────────────────────────────────────────────────────────────────────────────

_ASSESSMENT_DEADLINE_DAYS = 3   # candidates have 3 days to complete the assessment


def send_invitations_node(state: AssessmentState) -> AssessmentState:
    """
    For each Shortlisted candidate with a Pending CandidateAssessment,
    generates a unique HMAC-signed URL and sends the assessment invitation email.

    Immediately after dispatching all emails, calculates:
        deadline = utcnow() + 3 days
    and writes it to TechnicalAssessmentConfig.assessment_deadline so both
    the API enforcement layer and the post-deadline ranking scanner can use it.

    Guardrail: only emails candidates whose CandidateAssessment.status == 'Pending'.
    """
    from services.email_service import send_assessment_invitation_sync  # local import avoids circular

    shortlisted = state["shortlisted"]
    job_title = state["job_title"]
    req_id = state["requisition_id"]
    config_id = state["config_id"]

    if not shortlisted:
        return state

    db = SessionLocal()
    sent, failed = 0, 0
    try:
        for cand in shortlisted:
            app_id = cand["application_id"]
            email = cand.get("email", "")
            first_name = cand.get("first_name", "Candidate")

            if not email:
                continue

            assessment: Optional[CandidateAssessment] = (
                db.query(CandidateAssessment)
                .filter(CandidateAssessment.application_id == app_id)
                .first()
            )
            if not assessment or assessment.status != "Pending":
                continue

            assessment_url = build_assessment_url(assessment.assessment_id)
            success = send_assessment_invitation_sync(
                recipient_email=email,
                first_name=first_name,
                job_title=job_title,
                assessment_url=assessment_url,
            )
            if success:
                sent += 1
            else:
                failed += 1

        # ── Set assessment deadline immediately after dispatching all emails ──
        # deadline = now + 3 days; stored globally on TechnicalAssessmentConfig
        # so the API enforcement layer and the post-deadline ranking scanner
        # both read from one place.
        if sent > 0:
            try:
                deadline = datetime.utcnow() + timedelta(days=_ASSESSMENT_DEADLINE_DAYS)
                config = (
                    db.query(TechnicalAssessmentConfig)
                    .filter(TechnicalAssessmentConfig.config_id == config_id)
                    .first()
                )
                if config and config.assessment_deadline is None:
                    config.assessment_deadline = deadline
                    db.commit()
                    logger.info(
                        "[assessment_invite] Requisition %d — deadline set to %s (UTC).",
                        req_id, deadline.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            except Exception as exc:
                logger.warning(
                    "[assessment_invite] Could not set deadline for requisition %d: %s", req_id, exc
                )

    except Exception as exc:
        logger.exception("[assessment_invite] Error sending invitations for requisition %d.", req_id)
    finally:
        db.close()

    logger.info(
        "[assessment_invite] Requisition %d — %d invitations sent, %d failed.",
        req_id, sent, failed,
    )
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder + entry point
# ─────────────────────────────────────────────────────────────────────────────

def _route_after_context(state: AssessmentState) -> str:
    if state.get("error"):
        return "abort"
    if not state.get("shortlisted"):
        return "abort"
    return "generate"


def _route_after_generate(state: AssessmentState) -> str:
    if state.get("error"):
        return "abort"
    return "create"


def _route_after_create(state: AssessmentState) -> str:
    if state.get("error"):
        return "abort"
    return "invite"


def _build_assessment_graph():
    graph = StateGraph(AssessmentState)

    graph.add_node("load_context", load_context_node)
    graph.add_node("generate_questions", generate_questions_node)
    graph.add_node("create_assessments", create_assessments_node)
    graph.add_node("send_invitations", send_invitations_node)

    graph.set_entry_point("load_context")

    graph.add_conditional_edges(
        "load_context",
        _route_after_context,
        {"generate": "generate_questions", "abort": END},
    )
    graph.add_conditional_edges(
        "generate_questions",
        _route_after_generate,
        {"create": "create_assessments", "abort": END},
    )
    graph.add_conditional_edges(
        "create_assessments",
        _route_after_create,
        {"invite": "send_invitations", "abort": END},
    )
    graph.add_edge("send_invitations", END)

    return graph.compile()


_compiled_graph = None


def run_assessment_graph(requisition_id: int) -> AssessmentState:
    """
    Entry point — builds (or reuses) the compiled graph and executes it
    for the given requisition. Returns the final state dict.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_assessment_graph()

    initial: AssessmentState = {
        "requisition_id": requisition_id,
        "company_id": 0,
        "job_title": "",
        "seniority": "Mid-level",
        "config_id": 0,
        "template_id": None,
        "skills": [],
        "shortlisted": [],
        "questions_generated": False,
        "assessment_ids": [],
        "error": None,
    }

    logger.info("[assessment_graph] Starting workflow for requisition %d.", requisition_id)
    result: AssessmentState = _compiled_graph.invoke(initial)
    logger.info(
        "[assessment_graph] Workflow complete for requisition %d — "
        "%d assessments created, questions_generated=%s, error=%s",
        requisition_id,
        len(result.get("assessment_ids", [])),
        result.get("questions_generated"),
        result.get("error"),
    )
    return result

"""
Technical Assessment LangGraph Workflow
═══════════════════════════════════════
Triggered automatically after CV Ranking is finalized (dispatched from send_shortlist_emails).

Graph nodes (sequential):
  1. load_context_node        – load TechnicalAssessmentConfig, required skills, seniority,
                                and all Shortlisted applications from DB
  2. generate_questions_node  – maps skills → tools → concepts (knowledge DB Ground Truth),
                                calls GPT-4o-mini to generate exactly 15 questions grounded
                                ONLY on retrieved concepts; persists to:
                                  • generated_assessment_questions  (concept metadata + keywords)
                                  • assessment_template_questions   (grading-compatible source)
                                  • assessment_question_sets        (display order)
                                marks job_requisitions.assessment_generated = True
  3. create_assessments_node  – creates one CandidateAssessment row per Shortlisted application
  4. send_invitations_node    – sends unique HMAC-signed assessment invitation emails

Re-run safety:
  • generate_questions_node skips if job_requisitions.assessment_generated is already True.
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
from sqlmodel import Session as KnowledgeSession, select

from database.connection import SessionLocal
from helpers.config import get_settings
from knowledge_db.database import knowledge_engine
from knowledge_db.models import Concept, ConceptLevel, Tool
from models.db.application import Application
from models.db.assessment_question_set import AssessmentQuestionSet
from models.db.assessment_template import AssessmentTemplate
from models.db.assessment_template_question import AssessmentTemplateQuestion
from models.db.candidate import Candidate
from models.db.candidate_assessment import CandidateAssessment
from models.db.generated_assessment_question import GeneratedAssessmentQuestion
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.jr_knowledge_gap import JrKnowledgeGap
from models.db.requisition_required_skill import RequisitionRequiredSkill
from models.db.technical_assessment_config import TechnicalAssessmentConfig
from services.reviewer_service import regenerate_failed_questions, review_questions

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"
_TOTAL_QUESTIONS = 15


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
# LLM helpers
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


# ─────────────────────────────────────────────────────────────────────────────
# HMAC / URL helpers
# ─────────────────────────────────────────────────────────────────────────────

def generate_assessment_token(assessment_id: int) -> str:
    secret = os.getenv("SECRET_KEY", "assessment-default-secret-key")
    msg = f"assessment:{assessment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_assessment_token(assessment_id: int, token: str) -> bool:
    expected = generate_assessment_token(assessment_id)
    return hmac.compare_digest(expected, token)


def build_assessment_url(assessment_id: int) -> str:
    cfg = get_settings()
    token = generate_assessment_token(assessment_id)
    return f"{cfg.APP_BASE_URL}/assessment/{assessment_id}?token={token}"


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge DB helpers — Step 2 & 3 of the generation flow
# ─────────────────────────────────────────────────────────────────────────────

def _map_seniority_to_level(seniority: str) -> ConceptLevel:
    s = (seniority or "").lower()
    if any(x in s for x in ["junior", "entry", "beginner", "graduate", "intern"]):
        return ConceptLevel.beginner
    if any(x in s for x in ["mid", "intermediate", "associate"]):
        return ConceptLevel.mid
    return ConceptLevel.high  # senior, lead, principal, staff, etc.


def _load_concepts_and_gaps(
    skill_names: list[str],
    level: ConceptLevel,
) -> tuple[list[dict], list[str]]:
    """
    Step 2 — fuzzy-match each skill name to a Tool in the knowledge DB.
    Step 3 — retrieve Concepts for matched tools at the given level.

    Returns:
      (concepts, unmatched_skills)
      concepts         — list of concept dicts for use as LLM Ground Truth
      unmatched_skills — skill names that had no match in the knowledge DB;
                         these will be generated via AI general knowledge and
                         recorded in jr_knowledge_gaps.
    """
    if not skill_names:
        return [], []

    concepts_out: list[dict] = []
    matched_skills: set[str] = set()

    try:
        with KnowledgeSession(knowledge_engine) as ks:
            all_tools: list[Tool] = ks.exec(select(Tool)).all()
            matched_tool_ids: set[int] = set()

            for skill in skill_names:
                skill_lower = skill.lower().strip()
                skill_slug = skill_lower.replace(" ", "-")
                for tool in all_tools:
                    if tool.id in matched_tool_ids:
                        continue
                    if (
                        tool.name.lower() == skill_lower
                        or tool.slug.lower() == skill_slug
                        or skill_lower in tool.name.lower()
                        or tool.name.lower() in skill_lower
                    ):
                        matched_tool_ids.add(tool.id)
                        matched_skills.add(skill)
                        break

            unmatched_skills = [s for s in skill_names if s not in matched_skills]
            if unmatched_skills:
                logger.warning(
                    "[knowledge_db] %d skill(s) not found in knowledge DB — will use "
                    "AI general knowledge: %s",
                    len(unmatched_skills), ", ".join(unmatched_skills),
                )

            if not matched_tool_ids:
                # All skills are external — return early with empty concepts
                return [], unmatched_skills

            # Fetch concepts for matched tools at the target level
            matched_concepts: list[Concept] = ks.exec(
                select(Concept).where(
                    Concept.tool_id.in_(list(matched_tool_ids)),
                    Concept.level == level,
                )
            ).all()

            # Widen to all levels if the exact level has no concepts
            if not matched_concepts:
                logger.info(
                    "[knowledge_db] No concepts at level '%s' for matched tools — widening search.",
                    level.value,
                )
                matched_concepts = ks.exec(
                    select(Concept).where(Concept.tool_id.in_(list(matched_tool_ids)))
                ).all()

            tool_map = {t.id: t for t in all_tools}
            for c in matched_concepts:
                tool = tool_map.get(c.tool_id)
                concepts_out.append({
                    "concept_id": c.id,
                    "tool_id": c.tool_id,
                    "tool_name": tool.name if tool else "Unknown",
                    "level": c.level.value,
                    "concept_name": c.name,
                    "notes": c.notes or "",
                })

    except Exception as exc:
        logger.error("[knowledge_db] Failed to load concepts: %s", exc)
        # Treat all skills as unmatched so generation can still proceed
        return [], list(skill_names)

    logger.info(
        "[knowledge_db] Loaded %d concepts (matched). Unmatched skills: %d.",
        len(concepts_out), len(unmatched_skills),
    )
    return concepts_out, unmatched_skills


# ─────────────────────────────────────────────────────────────────────────────
# LLM Prompt — Step 4
# ─────────────────────────────────────────────────────────────────────────────

def _build_knowledge_question_prompt(
    concepts: list[dict],
    unmatched_skills: list[str],
    seniority: str,
    job_title: str,
) -> list[dict]:
    """
    Build the combined LLM prompt.

    Matched tools → injected as Ground Truth (concept name + notes).
    Unmatched skills → listed under EXTERNAL SKILLS; LLM uses general knowledge.
    All questions must include required_keywords regardless of source.
    """
    has_ground_truth = bool(concepts)
    has_external = bool(unmatched_skills)

    ground_truth_lines = [
        f"- [{c['tool_name']} | {c['level']}] {c['concept_name']}: {c['notes'] or '(no additional notes)'}"
        for c in concepts
    ]
    ground_truth_block = (
        "GROUND TRUTH — questions sourced from internal knowledge DB:\n"
        + "\n".join(ground_truth_lines)
        if has_ground_truth
        else ""
    )

    external_lines = [f"- {s} ({seniority} level)" for s in unmatched_skills]
    external_block = (
        "EXTERNAL SKILLS — not in internal DB; use AI general knowledge for these:\n"
        + "\n".join(external_lines)
        + "\n"
        + "  For external skill questions: set tool_name = exact skill name above, "
        + "concept_name = '<YourConceptName> [External Knowledge]'."
        if has_external
        else ""
    )

    sections = "\n\n".join(filter(None, [ground_truth_block, external_block]))

    if has_ground_truth and has_external:
        q_source_rule = (
            f"2. Distribute {_TOTAL_QUESTIONS} questions proportionally across Ground Truth "
            f"concepts AND External Skills. Every listed skill/concept must appear in at least "
            f"one question.\n"
            f"   Ground Truth questions: MUST use concept names/tools from the list above.\n"
            f"   External Knowledge questions: use your best general knowledge for those skills."
        )
        system_extra = (
            "You have both internal Ground Truth concepts AND external skills to cover. "
            "Use Ground Truth for known tools; use your general knowledge for external skills."
        )
    elif has_external:
        q_source_rule = (
            f"2. Generate all {_TOTAL_QUESTIONS} questions using your general AI knowledge "
            f"for the listed external skills. Every listed skill must appear in at least one question."
        )
        system_extra = (
            "No internal knowledge DB concepts are available. "
            "Use your general technical knowledge for all questions."
        )
    else:
        q_source_rule = (
            f"2. Every question MUST directly test one or more concepts from the Ground Truth above. "
            f"Never invent topics outside this list."
        )
        system_extra = (
            "You MUST generate questions ONLY from the Ground Truth concepts provided. "
            "Do NOT introduce tools, topics, or technologies not listed in the Ground Truth."
        )

    system_msg = (
        "You are a principal-level technical interviewer writing a high-stakes screening assessment. "
        "You ONLY respond with a valid JSON array — no markdown, no prose, no explanation, no emojis. "
        + system_extra
    )

    user_msg = (
        f"Design a rigorous technical assessment for a {seniority}-level {job_title} role.\n\n"
        f"{sections}\n\n"
        f"STRICT REQUIREMENTS:\n"
        f"1. Generate EXACTLY {_TOTAL_QUESTIONS} questions — no more, no less.\n"
        f"{q_source_rule}\n"
        f"3. Synthesize multiple concepts when possible: trade-offs, debugging scenarios, "
        f"   architectural decisions.\n"
        f"4. Question types: exactly 9 MCQ and 6 open_ended.\n"
        f"5. Complexity: scenario-based problems, production debugging, architectural trade-offs. "
        f"   PROHIBIT 'What is X?' or 'Define X' style questions.\n"
        f"6. No emojis. No decorative symbols. Plain, precise, technical English only.\n"
        f"7. For MCQ: correct_answer = the letter only (A, B, C, or D). "
        f"   required_keywords = [correct_letter, plus 2-4 key concept terms].\n"
        f"8. For open_ended: correct_answer = a concise model answer (3-5 sentences). "
        f"   required_keywords = list of 3-6 key technical terms the answer MUST contain.\n"
        f"9. Each MCQ option must be technically distinct and plausible — no obvious distractors.\n"
        f"10. concept_name: the concept this question tests. For Ground Truth use the exact "
        f"    concept name. For external skills use '<concept> [External Knowledge]'.\n"
        f"11. tool_name: for Ground Truth use exact tool name; for external skills use the exact "
        f"    skill name listed under EXTERNAL SKILLS.\n\n"
        f"Output MUST be a valid JSON array of exactly {_TOTAL_QUESTIONS} objects. Each object:\n"
        f"  - question_text: string\n"
        f"  - question_type: \"mcq\" or \"open_ended\"\n"
        f"  - options: [\"A. ...\", \"B. ...\", \"C. ...\", \"D. ...\"] for mcq, null for open_ended\n"
        f"  - correct_answer: string (letter only for mcq; concise model answer for open_ended)\n"
        f"  - required_keywords: list[str]  ← REQUIRED for every question, including MCQ\n"
        f"  - concept_name: string\n"
        f"  - tool_name: string\n"
        f"  - ai_grading_guide: string (1-3 sentences: key concepts required + partial vs full credit)\n\n"
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
            "[assessment_context] Requisition %d — %d skills, %d shortlisted candidates, "
            "assessment_generated=%s.",
            req_id, len(skills), len(shortlisted), jr.assessment_generated,
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
# Node 2 — Generate Questions (knowledge-grounded)
# ─────────────────────────────────────────────────────────────────────────────

def generate_questions_node(state: AssessmentState) -> AssessmentState:
    """
    Steps 2-5 of the knowledge-based assessment generator:

      Step 2 — map skill names → Tools in knowledge DB
      Step 3 — retrieve Concepts for matched tools at the JR seniority level (Ground Truth)
      Step 4 — LLM generates exactly 15 questions based ONLY on retrieved concepts
      Step 5 — persist to generated_assessment_questions + assessment_question_sets
               (also mirrors into AssessmentTemplate/AssessmentTemplateQuestion for grading)
               marks job_requisitions.assessment_generated = True

    Idempotent: skips if jr.assessment_generated is already True.
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

    db = SessionLocal()
    try:
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == req_id)
            .first()
        )
        if jr and jr.assessment_generated:
            logger.info(
                "[assessment_gen] Requisition %d already has generated questions — skipping.",
                req_id,
            )
            return {**state, "questions_generated": False, "template_id": existing_template_id}

        skill_names = [s["skill_name"] for s in skills]

        # ── Step 2 & 3: load concepts + identify unmatched skills ────────────
        level = _map_seniority_to_level(seniority)
        concepts, unmatched_skills = _load_concepts_and_gaps(skill_names, level)

        logger.info(
            "[assessment_gen] Requisition %d — %d knowledge DB concept(s), "
            "%d external skill(s): %s",
            req_id, len(concepts), len(unmatched_skills),
            ", ".join(unmatched_skills) if unmatched_skills else "none",
        )

        # ── Step 4: call LLM (combined Ground Truth + External prompt) ───────
        messages = _build_knowledge_question_prompt(concepts, unmatched_skills, seniority, job_title)
        raw_response = _llm_call(messages)

        # ── Create / reuse AssessmentTemplate (for grading compatibility) ─────
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
            # Clear all stale rows from both tables unconditionally
            db.query(GeneratedAssessmentQuestion).filter(
                GeneratedAssessmentQuestion.jr_id == req_id
            ).delete(synchronize_session=False)
            db.query(AssessmentQuestionSet).filter(
                AssessmentQuestionSet.jr_id == req_id
            ).delete(synchronize_session=False)
            db.query(AssessmentTemplateQuestion).filter(
                AssessmentTemplateQuestion.template_id == template_id
            ).delete(synchronize_session=False)

        # Build concept lookup by name for metadata enrichment
        concept_by_name = {c["concept_name"].lower(): c for c in concepts}
        # Set of unmatched skill names (lowercase) for fast external-question detection
        unmatched_lower: set[str] = {s.lower() for s in unmatched_skills}

        if raw_response is None:
            logger.warning("[assessment_gen] LLM returned None — saving fallback questions.")
            _save_fallback_questions_dual(db, template_id, req_id, skill_names[0], seniority, concepts)
            _record_knowledge_gaps(db, req_id, unmatched_skills)
            _mark_jr_generated(db, req_id)
            db.commit()
            return {**state, "questions_generated": True, "template_id": template_id}

        parsed, ok = _parse_json_response(raw_response)
        if not ok or not isinstance(parsed, list) or len(parsed) == 0:
            logger.warning(
                "[assessment_gen] Invalid JSON from LLM for requisition %d — using fallback.",
                req_id,
            )
            _save_fallback_questions_dual(db, template_id, req_id, skill_names[0], seniority, concepts)
            _record_knowledge_gaps(db, req_id, unmatched_skills)
            _mark_jr_generated(db, req_id)
            db.commit()
            return {**state, "questions_generated": True, "template_id": template_id}

        # ── Judge review: validate questions against Ground Truth (Step 4b) ───
        review_result = review_questions(
            questions=parsed[:_TOTAL_QUESTIONS],
            job_title=job_title,
            retrieved_concepts=concepts,
        )

        if review_result["status"] == "needs_revision" and review_result.get("improvements"):
            approved = review_result["approved_questions"]
            improvements = review_result["improvements"]

            # Build ordered list of failed questions using original indices
            sorted_failed_indices = sorted(
                {imp["question_index"] for imp in improvements if imp["question_index"] < len(parsed)}
            )
            failed_qs = [parsed[i] for i in sorted_failed_indices]

            # Remap original indices to 0-based so regeneration prompt pairs them correctly
            orig_to_new = {orig: new for new, orig in enumerate(sorted_failed_indices)}
            remapped_improvements = [
                {**imp, "question_index": orig_to_new[imp["question_index"]]}
                for imp in improvements
                if imp["question_index"] in orig_to_new
            ]

            logger.info(
                "[assessment_gen] Judge flagged %d/%d questions — regenerating.",
                len(failed_qs), len(parsed),
            )

            replacements = regenerate_failed_questions(
                failed_questions=failed_qs,
                improvements=remapped_improvements,
                concepts_payload=concepts,
            )
            # Merge: approved questions first, then replacements
            final_questions = list(approved) + list(replacements)
        else:
            final_questions = review_result.get("approved_questions") or parsed[:_TOTAL_QUESTIONS]

        # Cap at _TOTAL_QUESTIONS (judge may return slightly different counts)
        final_questions = final_questions[:_TOTAL_QUESTIONS]

        # ── Step 5: persist questions ─────────────────────────────────────────
        total_saved = 0
        for position, q in enumerate(final_questions, start=1):
            try:
                q_type = str(q.get("question_type", "open_ended")).lower()
                options_raw = q.get("options")
                options_json = json.dumps(options_raw) if options_raw and isinstance(options_raw, list) else None
                correct_answer = str(q.get("correct_answer") or "")
                ai_grading_guide = str(q.get("ai_grading_guide") or "")
                required_keywords = q.get("required_keywords") or []
                if not isinstance(required_keywords, list):
                    required_keywords = [str(required_keywords)]

                q_concept_name = str(q.get("concept_name") or skill_names[0])
                q_tool_name = str(q.get("tool_name") or skill_names[0])

                # Determine knowledge source: external if tool_name matches an unmatched skill
                is_external = q_tool_name.lower() in unmatched_lower

                if is_external:
                    # External knowledge question — no knowledge DB link
                    concept_id = None
                    tool_id = 0
                    concept_level = level.value
                    if "[External Knowledge]" not in q_concept_name:
                        q_concept_name = f"{q_concept_name} [External Knowledge]"
                else:
                    # Enrich with knowledge DB metadata
                    matched_concept = concept_by_name.get(q_concept_name.lower())
                    concept_id = matched_concept["concept_id"] if matched_concept else None
                    tool_id = matched_concept["tool_id"] if matched_concept else 0
                    concept_level = matched_concept["level"] if matched_concept else level.value

                # Mirror into AssessmentTemplateQuestion for grading pipeline
                stored_correct_answer = correct_answer
                if q_type == "open_ended" and ai_grading_guide:
                    stored_correct_answer = json.dumps({
                        "answer": correct_answer,
                        "grading_guide": ai_grading_guide,
                    })

                tq = AssessmentTemplateQuestion(
                    template_id=template_id,
                    question_text=str(q.get("question_text", "")),
                    question_type=q_type,
                    points=10,
                    correct_answer=stored_correct_answer,
                    options=options_json,
                )
                db.add(tq)
                db.flush()  # get tq.question_id before using it

                # Save to generated_assessment_questions (Step 5)
                gq = GeneratedAssessmentQuestion(
                    jr_id=req_id,
                    concept_id=concept_id,
                    tool_id=tool_id,
                    tool_name=q_tool_name,
                    level=concept_level,
                    concept_name=q_concept_name,
                    question_text=str(q.get("question_text", "")),
                    question_type=q_type,
                    required_keywords=required_keywords,
                    is_active=True,
                    template_question_id=tq.question_id,
                )
                db.add(gq)
                db.flush()  # get gq.id before using it

                # Save ordering to assessment_question_sets
                db.add(AssessmentQuestionSet(
                    jr_id=req_id,
                    question_id=gq.id,
                    position=position,
                ))

                total_saved += 1
            except Exception as exc:
                logger.warning("[assessment_gen] Skipping malformed question at position %d: %s", position, exc)

        _record_knowledge_gaps(db, req_id, unmatched_skills)
        _mark_jr_generated(db, req_id)
        db.commit()

        logger.info(
            "[assessment_gen] Saved %d/%d questions for requisition %d (template %d). "
            "External knowledge questions: %d skill(s).",
            total_saved, _TOTAL_QUESTIONS, req_id, template_id, len(unmatched_skills),
        )
        return {**state, "questions_generated": True, "template_id": template_id}

    except Exception as exc:
        db.rollback()
        logger.exception("[assessment_gen] DB error during question generation.")
        return {**state, "error": f"Question generation failed: {exc}"}
    finally:
        db.close()


def _mark_jr_generated(db, req_id: int) -> None:
    jr = db.query(JobRequisition).filter(JobRequisition.requisition_id == req_id).first()
    if jr:
        jr.assessment_generated = True
        jr.assessment_generated_at = datetime.utcnow()


def _record_knowledge_gaps(db, req_id: int, unmatched_skills: list[str]) -> None:
    """
    Idempotent: deletes existing gap rows for this JR then inserts fresh ones.
    No-op when unmatched_skills is empty.
    """
    if not unmatched_skills:
        return
    db.query(JrKnowledgeGap).filter(JrKnowledgeGap.jr_id == req_id).delete(synchronize_session=False)
    for skill in unmatched_skills:
        db.add(JrKnowledgeGap(
            jr_id=req_id,
            tool_name=skill,
            status="Missing in DB - LLM Generated",
        ))
    db.flush()
    logger.info(
        "[assessment_gen] Recorded %d knowledge gap(s) for requisition %d: %s",
        len(unmatched_skills), req_id, ", ".join(unmatched_skills),
    )


def _generate_from_skills_fallback(
    state: AssessmentState,
    db,
    existing_template_id: Optional[int],
    skill_names: list[str],
    seniority: str,
    company_id: int,
    job_title: str,
    req_id: int,
    config_id: int,
) -> AssessmentState:
    """
    Old-style generation from skill names alone, used when the knowledge DB
    returns no concepts. Saves to AssessmentTemplateQuestion only (no concept metadata).
    """
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

    messages = _build_legacy_question_prompt(skill_names, seniority, job_title)
    raw = _llm_call(messages)

    skill_label = skill_names[0] if skill_names else "unknown"

    if raw is None:
        _save_fallback_questions_dual(db, template_id, req_id, skill_label, seniority, [])
        _mark_jr_generated(db, req_id)
        db.commit()
        return {**state, "questions_generated": True, "template_id": template_id}

    parsed, ok = _parse_json_response(raw)
    if not ok or not isinstance(parsed, list) or len(parsed) == 0:
        _save_fallback_questions_dual(db, template_id, req_id, skill_label, seniority, [])
        _mark_jr_generated(db, req_id)
        db.commit()
        return {**state, "questions_generated": True, "template_id": template_id}

    for q in parsed[:_TOTAL_QUESTIONS]:
        try:
            q_type = str(q.get("question_type", "mcq")).lower()
            options_raw = q.get("options")
            options_json = json.dumps(options_raw) if options_raw and isinstance(options_raw, list) else None
            correct_answer = q.get("correct_answer") or ""
            ai_grading_guide = q.get("ai_grading_guide") or ""
            if q_type == "open_ended" and ai_grading_guide:
                correct_answer = json.dumps({"answer": correct_answer, "grading_guide": ai_grading_guide})
            q_text = str(q.get("question_text", ""))
            tq = AssessmentTemplateQuestion(
                template_id=template_id,
                question_text=q_text,
                question_type=q_type,
                points=10,
                correct_answer=correct_answer,
                options=options_json,
            )
            db.add(tq)
            db.flush()  # materialise question_id before inserting GeneratedAssessmentQuestion

            db.add(GeneratedAssessmentQuestion(
                jr_id=req_id,
                concept_id=None,
                tool_id=0,
                tool_name=skill_label,
                level=seniority.lower(),
                concept_name=skill_label,
                question_text=q_text,
                question_type=q_type,
                required_keywords=[],
                is_active=True,
                template_question_id=tq.question_id,
            ))
        except Exception as exc:
            logger.warning("[assessment_gen_fallback] Skipping malformed question: %s", exc)

    _mark_jr_generated(db, req_id)
    db.commit()
    return {**state, "questions_generated": True, "template_id": template_id}


def _build_legacy_question_prompt(skill_names: list[str], seniority: str, job_title: str) -> list[dict]:
    skills_list = ", ".join(skill_names)
    system_msg = (
        "You are a principal-level technical interviewer writing a high-stakes screening assessment. "
        "You ONLY respond with a valid JSON array — no markdown, no prose, no explanation, no emojis."
    )
    user_msg = (
        f"Design a rigorous technical assessment for a {seniority}-level {job_title} role.\n\n"
        f"Required skills: {skills_list}\n\n"
        f"STRICT REQUIREMENTS:\n"
        f"1. Generate EXACTLY 15 questions — no more, no less.\n"
        f"2. Questions MUST synthesize multiple skills from the list above.\n"
        f"3. Question types: include exactly 9 MCQ and 6 open_ended questions.\n"
        f"4. Complexity: scenario-based problems, production debugging, architectural trade-offs. "
        f"   PROHIBIT 'What is X?' or 'Define X' style questions.\n"
        f"5. No emojis. Plain, precise, technical English only.\n"
        f"6. Keep correct_answer for MCQ to the letter only (A, B, C, or D).\n"
        f"7. For open_ended, correct_answer must be a concise model answer (3-5 sentences max).\n"
        f"8. ai_grading_guide: 1-3 sentences listing key concepts and partial vs full credit criteria.\n"
        f"9. Each MCQ option must be technically distinct and plausible.\n\n"
        f"Output MUST be a valid JSON array of exactly 15 objects. Each object:\n"
        f"  - question_text: string\n"
        f"  - question_type: \"mcq\" or \"open_ended\"\n"
        f"  - options: [\"A. ...\", \"B. ...\", \"C. ...\", \"D. ...\"] for mcq, null for open_ended\n"
        f"  - correct_answer: string\n"
        f"  - ai_grading_guide: string\n\n"
        f"Respond with ONLY the JSON array. Begin immediately with '['."
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _save_fallback_questions_dual(
    db,
    template_id: int,
    req_id: int,
    skill_name: str,
    seniority: str,
    concepts: list[dict],
) -> None:
    """Save minimal fallback questions to both tables when the LLM is unavailable."""
    first_concept = concepts[0] if concepts else None
    fallback_items = [
        {
            "question_text": (
                f"Describe a production incident involving {skill_name} and explain how you "
                f"diagnosed the root cause and implemented a fix."
            ),
            "question_type": "open_ended",
            "correct_answer": json.dumps({
                "answer": (
                    f"Candidate should describe a specific incident, explain systematic debugging "
                    f"steps, identify root cause related to {skill_name}, and describe the fix."
                ),
                "grading_guide": (
                    f"Full marks for specific incident with clear diagnosis and fix. "
                    f"Partial marks for general debugging approach without specifics."
                ),
            }),
            "options": None,
            "required_keywords": [skill_name, "root cause", "diagnosis", "fix"],
        },
        {
            "question_text": (
                f"Which of the following BEST describes {skill_name} at the {seniority} level?"
            ),
            "question_type": "mcq",
            "correct_answer": "A",
            "options": json.dumps([
                f"A. Advanced proficiency with real-world {skill_name} deployment experience",
                f"B. Basic familiarity with {skill_name} concepts only",
                f"C. Theoretical knowledge of {skill_name} without practical application",
                f"D. No experience with {skill_name}",
            ]),
            "required_keywords": ["A", skill_name, "deployment"],
        },
    ]
    for position, item in enumerate(fallback_items, start=1):
        tq = AssessmentTemplateQuestion(
            template_id=template_id,
            question_text=item["question_text"],
            question_type=item["question_type"],
            points=10,
            correct_answer=item["correct_answer"],
            options=item.get("options"),
        )
        db.add(tq)
        db.flush()

        gq = GeneratedAssessmentQuestion(
            jr_id=req_id,
            concept_id=first_concept["concept_id"] if first_concept else None,
            tool_id=first_concept["tool_id"] if first_concept else 0,
            tool_name=first_concept["tool_name"] if first_concept else skill_name,
            level=first_concept["level"] if first_concept else seniority.lower(),
            concept_name=first_concept["concept_name"] if first_concept else skill_name,
            question_text=item["question_text"],
            question_type=item["question_type"],
            required_keywords=item["required_keywords"],
            is_active=True,
            template_question_id=tq.question_id,
        )
        db.add(gq)
        db.flush()

        db.add(AssessmentQuestionSet(jr_id=req_id, question_id=gq.id, position=position))


def _save_fallback_questions(db, template_id: int, skill_name: str, seniority: str) -> None:
    """Legacy fallback — saves to AssessmentTemplateQuestion only."""
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

_ASSESSMENT_DEADLINE_DAYS = 3


def send_invitations_node(state: AssessmentState) -> AssessmentState:
    """
    For each Shortlisted candidate with a Pending CandidateAssessment,
    generates a unique HMAC-signed URL and sends the assessment invitation email.
    Sets TechnicalAssessmentConfig.assessment_deadline = now + 3 days after dispatch.
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

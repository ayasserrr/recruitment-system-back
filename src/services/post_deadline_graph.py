"""
Post-Deadline Assessment Processing Pipeline
─────────────────────────────────────────────
4-node LangGraph workflow triggered after assessment_deadline passes.

Node 1  load_pool_node          – load all CandidateAssessments for requisition
Node 2  mark_no_shows_node      – mark Pending/In Progress as No-show, passed=False
Node 3  rank_pool_node          – sort Submitted by total_score, assign rank_in_pool
Node 4  generate_pool_report_node – GPT-4o-mini pool summary → pool_report column

Entry:  run_post_deadline_graph(requisition_id)
Returns dict with keys: error, no_show_count, ranked_count, pool_report
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from database.connection import SessionLocal
from models.db.application import Application
from models.db.assessment_report import AssessmentReport
from models.db.candidate_assessment import CandidateAssessment
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.technical_assessment_config import TechnicalAssessmentConfig

logger = logging.getLogger(__name__)

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_MODEL = "gpt-4o-mini"


# ── State ─────────────────────────────────────────────────────────────────────

class PostDeadlineState(TypedDict):
    requisition_id: int
    posting_id: Optional[int]
    config_id: Optional[int]
    assessments: list[dict]           # {assessment_id, status, total_score, candidate_name}
    no_show_count: int
    ranked_count: int
    pool_report: Optional[str]
    error: Optional[str]


# ── Node 1: load_pool_node ────────────────────────────────────────────────────

def load_pool_node(state: PostDeadlineState) -> PostDeadlineState:
    requisition_id = state["requisition_id"]
    db = SessionLocal()
    try:
        posting = (
            db.query(JobPosting)
            .filter(JobPosting.requisition_id == requisition_id)
            .first()
        )
        if not posting:
            return {**state, "error": f"No posting found for requisition {requisition_id}"}

        config = (
            db.query(TechnicalAssessmentConfig)
            .filter(TechnicalAssessmentConfig.requisition_id == requisition_id)
            .first()
        )

        rows = (
            db.query(CandidateAssessment)
            .join(Application, CandidateAssessment.application_id == Application.application_id)
            .filter(Application.posting_id == posting.posting_id)
            .all()
        )

        assessments = []
        for a in rows:
            app = db.query(Application).filter(Application.application_id == a.application_id).first()
            candidate_name = f"Candidate {a.application_id}"
            if app:
                try:
                    from models.db.candidate import Candidate
                    c = db.query(Candidate).filter(Candidate.candidate_id == app.candidate_id).first()
                    if c:
                        candidate_name = f"{c.first_name} {c.last_name}".strip()
                except Exception:
                    pass
            assessments.append({
                "assessment_id": a.assessment_id,
                "status": a.status,
                "total_score": float(a.total_score) if a.total_score is not None else None,
                "passed": a.passed,
                "candidate_name": candidate_name,
            })

        return {
            **state,
            "posting_id": posting.posting_id,
            "config_id": config.config_id if config else None,
            "assessments": assessments,
        }
    except Exception as exc:
        logger.exception("[post_deadline] load_pool_node error")
        return {**state, "error": str(exc)}
    finally:
        db.close()


# ── Node 2: mark_no_shows_node ────────────────────────────────────────────────

def mark_no_shows_node(state: PostDeadlineState) -> PostDeadlineState:
    if state.get("error"):
        return state

    no_show_ids = [
        a["assessment_id"]
        for a in state["assessments"]
        if a["status"] in ("Pending", "In Progress")
    ]
    if not no_show_ids:
        return {**state, "no_show_count": 0}

    db = SessionLocal()
    try:
        rows = (
            db.query(CandidateAssessment)
            .filter(CandidateAssessment.assessment_id.in_(no_show_ids))
            .all()
        )
        for row in rows:
            row.status = "No-show"
            row.passed = False
        db.commit()

        # Reflect updated status in state
        updated_assessments = []
        for a in state["assessments"]:
            if a["assessment_id"] in no_show_ids:
                updated_assessments.append({**a, "status": "No-show", "passed": False})
            else:
                updated_assessments.append(a)

        logger.info(
            "[post_deadline] Marked %d assessments as No-show for requisition %d.",
            len(no_show_ids), state["requisition_id"],
        )
        return {**state, "assessments": updated_assessments, "no_show_count": len(no_show_ids)}
    except Exception as exc:
        db.rollback()
        logger.exception("[post_deadline] mark_no_shows_node error")
        return {**state, "error": str(exc)}
    finally:
        db.close()


# ── Node 3: rank_pool_node ────────────────────────────────────────────────────

def rank_pool_node(state: PostDeadlineState) -> PostDeadlineState:
    if state.get("error"):
        return state

    submitted = sorted(
        [a for a in state["assessments"] if a["status"] == "Submitted" and a["total_score"] is not None],
        key=lambda x: x["total_score"],
        reverse=True,
    )

    if not submitted:
        return {**state, "ranked_count": 0}

    db = SessionLocal()
    try:
        updated = 0
        for rank, entry in enumerate(submitted, start=1):
            report = (
                db.query(AssessmentReport)
                .filter(AssessmentReport.assessment_id == entry["assessment_id"])
                .first()
            )
            if report:
                report.rank_in_pool = rank
                updated += 1
        db.commit()
        logger.info(
            "[post_deadline] Ranked %d submitted assessments for requisition %d.",
            updated, state["requisition_id"],
        )
        return {**state, "ranked_count": updated}
    except Exception as exc:
        db.rollback()
        logger.exception("[post_deadline] rank_pool_node error")
        return {**state, "error": str(exc)}
    finally:
        db.close()


# ── Node 4: generate_pool_report_node ────────────────────────────────────────

def generate_pool_report_node(state: PostDeadlineState) -> PostDeadlineState:
    if state.get("error"):
        return state

    assessments = state["assessments"]
    submitted = [a for a in assessments if a["status"] == "Submitted"]
    no_shows = [a for a in assessments if a["status"] == "No-show"]
    passed = [a for a in submitted if a.get("passed")]

    pool_lines = []
    for rank, a in enumerate(
        sorted(submitted, key=lambda x: x["total_score"] or 0, reverse=True), start=1
    ):
        pool_lines.append(
            f"  #{rank} {a['candidate_name']} — score: {a['total_score']}, passed: {a.get('passed')}"
        )

    pool_summary = "\n".join(pool_lines) or "  (no submissions)"

    prompt = (
        f"You are an HR analytics assistant. Summarize the technical assessment pool results.\n\n"
        f"Requisition ID: {state['requisition_id']}\n"
        f"Total candidates invited: {len(assessments)}\n"
        f"Submitted: {len(submitted)}\n"
        f"No-shows: {len(no_shows)}\n"
        f"Passed: {len(passed)}\n\n"
        f"Ranked submissions (best first):\n{pool_summary}\n\n"
        f"Write a concise 3–5 sentence executive summary covering:\n"
        f"1. Overall participation rate\n"
        f"2. Performance distribution (pass rate, score range)\n"
        f"3. A brief recommendation on advancing candidates\n\n"
        f"Return ONLY the plain text summary — no JSON, no markdown headers."
    )

    pool_report = _call_gpt(prompt)

    # Persist to TechnicalAssessmentConfig.pool_report
    if state.get("config_id") and pool_report:
        db = SessionLocal()
        try:
            config = (
                db.query(TechnicalAssessmentConfig)
                .filter(TechnicalAssessmentConfig.config_id == state["config_id"])
                .first()
            )
            if config:
                config.pool_report = pool_report
                db.commit()
                logger.info(
                    "[post_deadline] Pool report saved for config %d.", state["config_id"]
                )
        except Exception as exc:
            db.rollback()
            logger.warning("[post_deadline] Could not save pool_report: %s", exc)
        finally:
            db.close()

    return {**state, "pool_report": pool_report}


# ── GPT helper ────────────────────────────────────────────────────────────────

def _call_gpt(prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = httpx.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {_OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("[post_deadline] GPT attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return "Pool report generation failed — manual review required."


# ── Graph assembly ────────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(PostDeadlineState)
    g.add_node("load_pool", load_pool_node)
    g.add_node("mark_no_shows", mark_no_shows_node)
    g.add_node("rank_pool", rank_pool_node)
    g.add_node("generate_pool_report", generate_pool_report_node)

    g.set_entry_point("load_pool")
    g.add_edge("load_pool", "mark_no_shows")
    g.add_edge("mark_no_shows", "rank_pool")
    g.add_edge("rank_pool", "generate_pool_report")
    g.add_edge("generate_pool_report", END)
    return g


_compiled_graph = _build_graph().compile()


def run_post_deadline_graph(requisition_id: int) -> dict:
    initial: PostDeadlineState = {
        "requisition_id": requisition_id,
        "posting_id": None,
        "config_id": None,
        "assessments": [],
        "no_show_count": 0,
        "ranked_count": 0,
        "pool_report": None,
        "error": None,
    }
    result = _compiled_graph.invoke(initial)
    return {
        "error": result.get("error"),
        "no_show_count": result.get("no_show_count", 0),
        "ranked_count": result.get("ranked_count", 0),
        "pool_report": result.get("pool_report"),
    }

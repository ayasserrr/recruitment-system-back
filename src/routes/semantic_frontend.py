"""
Semantic Analysis endpoints (dashboard).

GET  /api/v1/jobs/{jobId}/semantic          — results + stats
POST /api/v1/jobs/{jobId}/semantic/run      — trigger async analysis
GET  /api/v1/jobs/{jobId}/semantic/status   — poll progress
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_current_company
from database.connection import get_db
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.candidate_cv import CandidateCV
from models.db.cv_education import CVEducation
from models.db.cv_experience import CVExperience
from models.db.cv_project import CVProject
from models.db.cv_skill import CVSkill
from models.db.job_posting import JobPosting
from models.db.job_requisition import JobRequisition
from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.semantic_matched_skill import SemanticMatchedSkill
from models.schemas.frontend_schemas import (
    SemanticCandidate,
    SemanticResponse,
    SemanticRunResponse,
    SemanticStats,
    SemanticStatusResponse,
    match_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["semantic-analysis"])


def _require_posting(job_id: int, company_id: int, db: Session):
    jr = (
        db.query(JobRequisition)
        .filter(
            JobRequisition.requisition_id == job_id,
            JobRequisition.company_id == company_id,
        )
        .first()
    )
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    posting = (
        db.query(JobPosting)
        .filter(JobPosting.requisition_id == job_id)
        .first()
    )
    return jr, posting


def _years_label(months: Optional[int]) -> Optional[str]:
    if not months:
        return None
    years = months // 12
    rem = months % 12
    if years and rem:
        return f"{years}y {rem}m"
    if years:
        return f"{years}+ years"
    return f"{rem} months"


# ── GET /api/v1/jobs/{jobId}/semantic ─────────────────────────────────────────

@router.get("/{job_id}/semantic", response_model=SemanticResponse, summary="Semantic analysis results")
def get_semantic(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_posting(job_id, ctx["company_id"], db)
    if not posting:
        return SemanticResponse(
            jobId=job_id,
            stats=SemanticStats(
                totalCandidates=0, processed=0, highMatch=0,
                mediumMatch=0, lowMatch=0, avgScore=0.0,
            ),
            candidates=[],
        )

    posting_id = posting.posting_id

    # ── Aggregate stats ────────────────────────────────────────────────────────
    total_apps: int = (
        db.query(func.count(Application.application_id))
        .filter(Application.posting_id == posting_id)
        .scalar()
    ) or 0

    reports = (
        db.query(
            SemanticAnalysisReport,
            Application,
            Candidate,
        )
        .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .filter(Application.posting_id == posting_id)
        .order_by(SemanticAnalysisReport.match_percentage.desc())
        .all()
    )

    processed = len(reports)
    scores = [float(r.SemanticAnalysisReport.match_percentage or 0) for r in reports]
    high = sum(1 for s in scores if s >= 70)
    medium = sum(1 for s in scores if 40 <= s < 70)
    low = sum(1 for s in scores if s < 40)
    avg_score = round(sum(scores) / processed, 2) if processed else 0.0

    # ── Build skill map ────────────────────────────────────────────────────────
    if reports:
        report_ids = [r.SemanticAnalysisReport.report_id for r in reports]
        skills_rows = (
            db.query(SemanticMatchedSkill)
            .filter(SemanticMatchedSkill.report_id.in_(report_ids))
            .all()
        )
        skills_map: dict[int, List[str]] = {}
        for sk in skills_rows:
            if sk.match_type != "missing":
                skills_map.setdefault(sk.report_id, []).append(sk.skill_name)
    else:
        skills_map = {}

    # ── Build candidate list ───────────────────────────────────────────────────
    candidates: List[SemanticCandidate] = []
    for row in reports:
        report: SemanticAnalysisReport = row.SemanticAnalysisReport
        app: Application = row.Application
        cand: Candidate = row.Candidate

        # Education
        education_str: Optional[str] = None
        if cand.education_level:
            education_str = cand.education_level
            if cand.field_of_study:
                education_str += f" in {cand.field_of_study}"

        # Experience label from DB
        exp_label: Optional[str] = None
        if cand.years_of_experience:
            exp_label = f"{cand.years_of_experience}+ years"

        # Projects from CV
        projects: List[str] = []
        if app.cv_id:
            projects = [
                p.project_name
                for p in db.query(CVProject)
                .filter(CVProject.cv_id == app.cv_id)
                .limit(5)
                .all()
                if p.project_name
            ]

        score = float(report.match_percentage or 0)
        candidates.append(
            SemanticCandidate(
                id=cand.candidate_id,
                name=f"{cand.first_name} {cand.last_name}".strip(),
                score=round(score, 1),
                match=match_label(score),
                skills=skills_map.get(report.report_id, []),
                email=cand.email,
                phone=cand.phone,
                experience=exp_label,
                education=education_str,
                summary=cand.professional_summary or report.recommendation_summary,
                projects=projects,
            )
        )

    # ── Processing time ────────────────────────────────────────────────────────
    processing_time: Optional[str] = None
    if reports:
        min_gen = (
            db.query(func.min(SemanticAnalysisReport.generated_at))
            .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        )
        max_gen = (
            db.query(func.max(SemanticAnalysisReport.generated_at))
            .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
            .filter(Application.posting_id == posting_id)
            .scalar()
        )
        if min_gen and max_gen:
            diff = (max_gen - min_gen).total_seconds()
            if diff < 60:
                processing_time = f"{int(diff)}s"
            else:
                processing_time = f"{int(diff // 60)}m {int(diff % 60)}s"

    return SemanticResponse(
        jobId=job_id,
        processingTime=processing_time,
        stats=SemanticStats(
            totalCandidates=total_apps,
            processed=processed,
            highMatch=high,
            mediumMatch=medium,
            lowMatch=low,
            avgScore=avg_score,
        ),
        candidates=candidates,
    )


# ── POST /api/v1/jobs/{jobId}/semantic/run ────────────────────────────────────

@router.post(
    "/{job_id}/semantic/run",
    response_model=SemanticRunResponse,
    summary="Trigger semantic analysis (async)",
)
def run_semantic(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_posting(job_id, ctx["company_id"], db)

    # Dispatch Celery task if available, otherwise return pending task ID
    task_id = str(uuid.uuid4())
    try:
        from tasks.ranking_tasks import process_cv_ranking
        result = process_cv_ranking.delay(job_id)
        task_id = result.id
    except Exception:
        logger.warning("[semantic/run] Could not dispatch Celery task for JR %d.", job_id)

    return SemanticRunResponse(taskId=task_id, status="running")


# ── GET /api/v1/jobs/{jobId}/semantic/status ──────────────────────────────────

@router.get(
    "/{job_id}/semantic/status",
    response_model=SemanticStatusResponse,
    summary="Poll semantic analysis progress",
)
def semantic_status(
    job_id: int,
    ctx: dict = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    jr, posting = _require_posting(job_id, ctx["company_id"], db)

    if not posting:
        return SemanticStatusResponse(status="pending", progress=0)

    total_apps: int = (
        db.query(func.count(Application.application_id))
        .filter(Application.posting_id == posting.posting_id)
        .scalar()
    ) or 0

    processed: int = (
        db.query(func.count(SemanticAnalysisReport.report_id))
        .join(Application, Application.application_id == SemanticAnalysisReport.application_id)
        .filter(Application.posting_id == posting.posting_id)
        .scalar()
    ) or 0

    if total_apps == 0:
        return SemanticStatusResponse(status="pending", progress=0)

    progress = int((processed / total_apps) * 100)
    run_status = "completed" if processed >= total_apps else (
        "running" if jr.processing_status == "processing" else (
            "failed" if jr.processing_status == "error" else "pending"
        )
    )
    return SemanticStatusResponse(status=run_status, progress=progress)

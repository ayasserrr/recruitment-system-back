"""
Orchestrator for the full relative grading pipeline.

run_relative_grading_pipeline(jr_id, db)
  1. Load concepts + candidates from DB
  2. Run 3-phase pipeline
  3. Persist results to assessment_leaderboards (idempotent)
  4. Delete stale output artifacts for this JR (idempotency)
  5. Generate hr_report.xlsx → output/
  6. Write results.json → output/
  7. Update job_requisitions.hr_report_path + results_json_path
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from models.db.assessment_leaderboard import AssessmentLeaderboard
from models.db.job_requisition import JobRequisition
from services.relative_grading.data_loader import load_candidates, load_concepts
from services.relative_grading.grading_config import load_config
from services.relative_grading.hr_report import generate_hr_report
from services.relative_grading.pipeline import run_pipeline

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "output"


def run_relative_grading_pipeline(jr_id: int, db: Session) -> dict:
    """
    Full pipeline entry point — idempotent: clears old leaderboard rows and
    stale output files before writing fresh results.

    Returns:
      {
        "jr_id": int,
        "n_passed": int,
        "n_rejected": int,
        "top_candidate": str | None,
        "top_score": float | None,
        "report_path": str | None,
        "results_json_path": str | None,
        "error": str | None,
      }
    """
    config = load_config()

    try:
        concepts = load_concepts(jr_id, db)
        candidates = load_candidates(jr_id, db)
    except Exception as exc:
        logger.exception("[runner] Data loading failed for jr_id=%d.", jr_id)
        return {"jr_id": jr_id, "error": str(exc)}

    if not concepts:
        msg = f"No active concepts with required_keywords found for jr_id={jr_id}."
        logger.warning("[runner] %s", msg)
        return {"jr_id": jr_id, "error": msg}

    if not candidates:
        msg = f"No submitted candidates found for jr_id={jr_id}."
        logger.warning("[runner] %s", msg)
        return {"jr_id": jr_id, "error": msg}

    try:
        leaderboard, keyword_gap_report = run_pipeline(candidates, concepts, config)
    except Exception as exc:
        logger.exception("[runner] Pipeline failed for jr_id=%d.", jr_id)
        return {"jr_id": jr_id, "error": str(exc)}

    # ── Persist to DB (idempotent) ────────────────────────────────────────────
    try:
        _persist_leaderboard(jr_id, leaderboard, db)
    except Exception as exc:
        logger.exception("[runner] DB persist failed for jr_id=%d.", jr_id)
        return {"jr_id": jr_id, "error": str(exc)}

    # ── Remove stale artifacts before writing new ones ────────────────────────
    _cleanup_stale_artifacts(jr_id)

    # ── Generate HR Excel report ──────────────────────────────────────────────
    report_path_str: str | None = None
    try:
        report_path = generate_hr_report(
            leaderboard=leaderboard,
            keyword_gap_report=keyword_gap_report,
            jr_id=jr_id,
            output_dir=_OUTPUT_DIR,
        )
        report_path_str = str(report_path)
    except Exception as exc:
        logger.warning("[runner] HR report generation failed for jr_id=%d: %s", jr_id, exc)

    # ── Write results.json ────────────────────────────────────────────────────
    json_path_str: str | None = None
    try:
        json_path_str = _write_results_json(jr_id, leaderboard, keyword_gap_report)
    except Exception as exc:
        logger.warning("[runner] results.json write failed for jr_id=%d: %s", jr_id, exc)

    # ── Persist both artifact paths to DB in a single commit ─────────────────
    try:
        _save_artifact_paths(jr_id, report_path_str, json_path_str, db)
    except Exception as exc:
        logger.warning("[runner] Artifact path DB save failed for jr_id=%d: %s", jr_id, exc)

    passing = [e for e in leaderboard if not e["reject"]]
    rejected = [e for e in leaderboard if e["reject"]]
    top = passing[0] if passing else None

    logger.info(
        "[runner] Done. jr_id=%d | %d passed, %d rejected. Top: %s @ %.3f",
        jr_id,
        len(passing),
        len(rejected),
        top["name"] if top else "—",
        top["final_score"] if top else 0.0,
    )

    return {
        "jr_id": jr_id,
        "n_passed": len(passing),
        "n_rejected": len(rejected),
        "top_candidate": top["name"] if top else None,
        "top_score": top["final_score"] if top else None,
        "report_path": report_path_str,
        "results_json_path": json_path_str,
        "error": None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _persist_leaderboard(jr_id: int, leaderboard: list[dict], db: Session) -> None:
    """Delete old entries then insert fresh rows — keeps the table idempotent."""
    deleted = (
        db.query(AssessmentLeaderboard)
        .filter(AssessmentLeaderboard.jr_id == jr_id)
        .delete(synchronize_session=False)
    )
    if deleted:
        logger.info("[runner] Cleared %d old leaderboard rows for jr_id=%d.", deleted, jr_id)
    db.flush()

    for entry in leaderboard:
        row = AssessmentLeaderboard(
            jr_id=jr_id,
            candidate_id=entry["candidate_id"],
            application_id=entry["application_id"],
            assessment_id=entry["assessment_id"],
            rank=entry.get("rank"),
            final_score=entry["final_score"],
            segment=entry["segment"],
            reject=entry["reject"],
            reject_reason=entry.get("reject_reason"),
            avg_depth=entry["avg_depth"],
            per_question_detail=entry.get("per_question"),
        )
        db.add(row)

    db.commit()
    logger.info("[runner] Persisted %d leaderboard rows for jr_id=%d.", len(leaderboard), jr_id)


def _cleanup_stale_artifacts(jr_id: int) -> None:
    """
    Delete any existing hr_report and results files for this JR from output/.
    Prevents stale timestamped files accumulating across re-runs.
    """
    if not _OUTPUT_DIR.exists():
        return
    prefixes = (f"hr_report_jr{jr_id}_", f"results_jr{jr_id}_")
    removed = 0
    for f in _OUTPUT_DIR.iterdir():
        if f.is_file() and f.name.startswith(prefixes):
            try:
                f.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("[runner] Could not delete stale file %s: %s", f, exc)
    if removed:
        logger.info("[runner] Removed %d stale artifact(s) for jr_id=%d.", removed, jr_id)


def _write_results_json(
    jr_id: int,
    leaderboard: list[dict],
    keyword_gap_report: dict,
) -> str:
    """Write results JSON to output/ and return the absolute path string."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = _OUTPUT_DIR / f"results_jr{jr_id}_{timestamp}.json"

    serialisable_lb = []
    for entry in leaderboard:
        row = {k: v for k, v in entry.items() if k != "per_question"}
        row["final_score"] = float(entry["final_score"]) if entry["final_score"] is not None else None
        row["avg_depth"] = float(entry["avg_depth"]) if entry["avg_depth"] is not None else None
        row["per_question"] = entry.get("per_question", {})
        serialisable_lb.append(row)

    payload = {
        "jr_id": jr_id,
        "generated_at": datetime.utcnow().isoformat(),
        "leaderboard": serialisable_lb,
        "keyword_gap_report": keyword_gap_report,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("[runner] results.json written: %s", out_path)
    return str(out_path)


def _save_artifact_paths(
    jr_id: int,
    report_path_str: str | None,
    json_path_str: str | None,
    db: Session,
) -> None:
    """Persist both artifact paths to job_requisitions in a single DB commit."""
    jr = db.query(JobRequisition).filter(JobRequisition.requisition_id == jr_id).first()
    if not jr:
        logger.warning("[runner] JobRequisition %d not found — cannot save artifact paths.", jr_id)
        return
    if report_path_str is not None:
        jr.hr_report_path = report_path_str
    if json_path_str is not None:
        jr.results_json_path = json_path_str
    db.commit()
    logger.info(
        "[runner] Artifact paths saved for jr_id=%d | excel=%s | json=%s",
        jr_id, report_path_str, json_path_str,
    )

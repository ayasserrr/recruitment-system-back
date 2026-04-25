"""
Relative auto-grading pipeline for technical candidate assessments.

Phases:
  1. Keyword coverage with GPT-4o-mini semantic fallback
  2. Comparative depth scoring — normalised against pool maximum
  3. Ranking, rejection, deterministic tiebreaking via GPT-4o

Entry point: run_relative_grading_pipeline(jr_id, db)
"""
from services.relative_grading.pipeline import run_pipeline
from services.relative_grading.data_loader import load_candidates, load_concepts
from services.relative_grading.grading_config import load_config

__all__ = ["run_pipeline", "load_candidates", "load_concepts", "load_config"]

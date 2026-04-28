"""add_ensemble_scoring_columns

Revision ID: f9e8d7c6b5a4
Revises: 71c8c1f3853d, h2i3j4k5l6m7
Create Date: 2026-04-27

Adds per-model ensemble scoring columns to:
  - assessment_answers    (7 new columns: NLI, BGE, MPNet, QA, TF-IDF, ensemble, shap)
  - assessment_reports    (1 new column: shap_summary)
  - hr_interview_sessions (7 new columns: transcript, emotion, sentiment, NLI, depth, shap_json, shap_summary)

All new columns are nullable — existing rows are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f9e8d7c6b5a4'
down_revision: Union[str, tuple] = ('71c8c1f3853d', 'h2i3j4k5l6m7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def _add_if_missing(table: str, col_name: str, col_type) -> None:
    """Idempotent column addition — no-op if the column already exists."""
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    existing  = {c["name"] for c in inspector.get_columns(table)}
    if col_name not in existing:
        op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def upgrade() -> None:
    # ── assessment_answers ────────────────────────────────────────────────────
    _add_if_missing("assessment_answers", "nli_score",            sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "semantic_bge_score",   sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "semantic_mpnet_score", sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "roberta_qa_score",     sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "tfidf_score",          sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "ensemble_score",       sa.Numeric(6, 4))
    _add_if_missing("assessment_answers", "shap_json",            sa.Text())

    # ── assessment_reports ────────────────────────────────────────────────────
    _add_if_missing("assessment_reports", "shap_summary", sa.Text())

    # ── hr_interview_sessions ─────────────────────────────────────────────────
    _add_if_missing("hr_interview_sessions", "transcript",          sa.Text())
    _add_if_missing("hr_interview_sessions", "emotion_score",       sa.Numeric(6, 4))
    _add_if_missing("hr_interview_sessions", "sentiment_score",     sa.Numeric(6, 4))
    _add_if_missing("hr_interview_sessions", "nli_align_score",     sa.Numeric(6, 4))
    _add_if_missing("hr_interview_sessions", "semantic_depth_score",sa.Numeric(6, 4))
    _add_if_missing("hr_interview_sessions", "shap_json",           sa.Text())
    _add_if_missing("hr_interview_sessions", "shap_summary",        sa.Text())


def downgrade() -> None:
    # assessment_answers
    for col in ["shap_json", "ensemble_score", "tfidf_score",
                "roberta_qa_score", "semantic_mpnet_score",
                "semantic_bge_score", "nli_score"]:
        op.drop_column("assessment_answers", col)

    # assessment_reports
    op.drop_column("assessment_reports", "shap_summary")

    # hr_interview_sessions
    for col in ["shap_summary", "shap_json", "semantic_depth_score",
                "nli_align_score", "sentiment_score", "emotion_score", "transcript"]:
        op.drop_column("hr_interview_sessions", col)

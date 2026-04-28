"""add_tech_interview_analysis_columns

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-04-28

Adds 6 nullable columns to technical_interview_sessions:
  - codebert_score        (Numeric 6,4) — CodeBERT semantic depth score
  - roberta_depth_score   (Numeric 6,4) — RoBERTa-QA extraction score
  - nli_technical_score   (Numeric 6,4) — DeBERTa NLI technical alignment
  - tfidf_technical_score (Numeric 6,4) — TF-IDF keyword coverage
  - shap_json             (Text)         — per-model SHAP values as JSON
  - shap_summary          (Text)         — human-readable SHAP narrative

These columns are populated by tech_interview_node during the Final Ranking
pipeline and enable the ensemble technical depth score (30% weight) to
replace the raw overall_score for a more interpretable ranking.

All new columns are nullable — existing rows are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, tuple] = 'b7c8d9e0f1a2'
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
    _add_if_missing("technical_interview_sessions", "codebert_score",        sa.Numeric(6, 4))
    _add_if_missing("technical_interview_sessions", "roberta_depth_score",   sa.Numeric(6, 4))
    _add_if_missing("technical_interview_sessions", "nli_technical_score",   sa.Numeric(6, 4))
    _add_if_missing("technical_interview_sessions", "tfidf_technical_score", sa.Numeric(6, 4))
    _add_if_missing("technical_interview_sessions", "shap_json",             sa.Text())
    _add_if_missing("technical_interview_sessions", "shap_summary",          sa.Text())


def downgrade() -> None:
    for col in ["shap_summary", "shap_json", "tfidf_technical_score",
                "nli_technical_score", "roberta_depth_score", "codebert_score"]:
        op.drop_column("technical_interview_sessions", col)

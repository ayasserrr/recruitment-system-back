"""add_shap_columns_to_final_rankings

Revision ID: b7c8d9e0f1a2
Revises: f9e8d7c6b5a4
Create Date: 2026-04-27

Adds two nullable SHAP explanation columns to final_rankings:
  - shap_json    (Text) — JSON dict {"screening": φ, "assessment": φ, "hr_interview": φ}
  - shap_summary (Text) — human-readable cross-phase narrative

These columns are populated by score_candidates_node during the final ranking run
and reflect the new 30/35/35 weighting (Screening/Assessment/HR Interview).
All new columns are nullable — existing rows are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, tuple] = 'f9e8d7c6b5a4'
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
    _add_if_missing("final_rankings", "shap_json",    sa.Text())
    _add_if_missing("final_rankings", "shap_summary", sa.Text())


def downgrade() -> None:
    op.drop_column("final_rankings", "shap_summary")
    op.drop_column("final_rankings", "shap_json")

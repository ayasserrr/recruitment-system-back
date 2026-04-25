"""add_hr_explanations_to_semantic_analysis_reports

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - Adds hr_explanation_text and hr_explanation_json columns to semantic_analysis_reports
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("semantic_analysis_reports")]

    if "hr_explanation_text" not in existing_cols:
        op.add_column(
            "semantic_analysis_reports",
            sa.Column("hr_explanation_text", sa.Text(), nullable=True),
        )

    if "hr_explanation_json" not in existing_cols:
        op.add_column(
            "semantic_analysis_reports",
            sa.Column("hr_explanation_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("semantic_analysis_reports", "hr_explanation_json")
    op.drop_column("semantic_analysis_reports", "hr_explanation_text")

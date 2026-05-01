"""add summary and risk_score to final_rankings

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-04-29

Adds two columns to final_rankings:
  summary    — full per-candidate narrative (generate_final_candidate_summary)
  risk_score — probabilistic RF-1/RF-2 sigmoid risk score (0-1)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "final_rankings",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "final_rankings",
        sa.Column("risk_score", sa.Numeric(6, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("final_rankings", "risk_score")
    op.drop_column("final_rankings", "summary")

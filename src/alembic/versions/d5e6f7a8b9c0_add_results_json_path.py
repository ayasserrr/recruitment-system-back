"""add_results_json_path

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - Adds results_json_path column to job_requisitions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("job_requisitions")]

    if "results_json_path" not in existing_cols:
        op.add_column(
            "job_requisitions",
            sa.Column("results_json_path", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("job_requisitions", "results_json_path")

"""add processing_lock columns to job_requisitions

Revision ID: b0c1d2e3f4a5
Revises: 5c07e5af9baa
Create Date: 2026-04-25 20:00:00.000000

Adds two columns to job_requisitions:
  processing_status       VARCHAR(20) NOT NULL DEFAULT 'idle'
      Values: 'idle' | 'processing' | 'error'
      Used by Celery beat scanners to skip JRs that are currently being
      processed, preventing duplicate task dispatches.

  processing_started_at   TIMESTAMP NULL
      Set when processing_status transitions to 'processing'.
      Used for stale-lock detection (lock older than 30 min is overrideable).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "5c07e5af9baa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_requisitions",
        sa.Column(
            "processing_status",
            sa.String(20),
            nullable=False,
            server_default="idle",
        ),
    )
    op.add_column(
        "job_requisitions",
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_jr_processing_status",
        "job_requisitions",
        ["processing_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_jr_processing_status", table_name="job_requisitions")
    op.drop_column("job_requisitions", "processing_started_at")
    op.drop_column("job_requisitions", "processing_status")

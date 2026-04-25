"""add_interview_phase_and_red_flag

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - job_requisitions: add interview_notified (BOOLEAN), interview_deadline (TIMESTAMP)
  - final_rankings:   add red_flag (BOOLEAN), red_flag_reason (TEXT)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── job_requisitions ──────────────────────────────────────────────────────
    jr_cols = {c["name"] for c in inspector.get_columns("job_requisitions")}

    if "interview_notified" not in jr_cols:
        op.add_column(
            "job_requisitions",
            sa.Column(
                "interview_notified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if "interview_deadline" not in jr_cols:
        op.add_column(
            "job_requisitions",
            sa.Column("interview_deadline", sa.DateTime(), nullable=True),
        )

    # ── final_rankings ────────────────────────────────────────────────────────
    fr_cols = {c["name"] for c in inspector.get_columns("final_rankings")}

    if "red_flag" not in fr_cols:
        op.add_column(
            "final_rankings",
            sa.Column(
                "red_flag",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if "red_flag_reason" not in fr_cols:
        op.add_column(
            "final_rankings",
            sa.Column("red_flag_reason", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("final_rankings", "red_flag_reason")
    op.drop_column("final_rankings", "red_flag")
    op.drop_column("job_requisitions", "interview_deadline")
    op.drop_column("job_requisitions", "interview_notified")

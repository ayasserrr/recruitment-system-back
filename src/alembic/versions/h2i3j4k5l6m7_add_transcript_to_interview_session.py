"""add transcript to technical_interview_sessions

Revision ID: h2i3j4k5l6m7
Revises: 47463f4ad0b2, 5c07e5af9baa
Create Date: 2026-04-27 00:00:00.000000

Changes:
  - technical_interview_sessions: add transcript (Text, nullable)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h2i3j4k5l6m7"
down_revision: Union[str, tuple] = ("47463f4ad0b2", "5c07e5af9baa")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("technical_interview_sessions")}

    if "transcript" not in cols:
        op.add_column(
            "technical_interview_sessions",
            sa.Column("transcript", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("technical_interview_sessions", "transcript")

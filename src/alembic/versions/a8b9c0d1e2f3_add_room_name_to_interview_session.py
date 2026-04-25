"""add_room_name_language_mode_to_technical_interview_session

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - technical_interview_sessions: add room_name (VARCHAR 120, unique, indexed)
  - technical_interview_sessions: add language (VARCHAR 10)
  - technical_interview_sessions: add mode (VARCHAR 20)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("technical_interview_sessions")}

    if "room_name" not in cols:
        op.add_column(
            "technical_interview_sessions",
            sa.Column("room_name", sa.String(120), nullable=True),
        )
        op.create_unique_constraint(
            "uq_tis_room_name",
            "technical_interview_sessions",
            ["room_name"],
        )
        op.create_index(
            "ix_tis_room_name",
            "technical_interview_sessions",
            ["room_name"],
        )

    if "language" not in cols:
        op.add_column(
            "technical_interview_sessions",
            sa.Column("language", sa.String(10), nullable=True, server_default="en"),
        )

    if "mode" not in cols:
        op.add_column(
            "technical_interview_sessions",
            sa.Column("mode", sa.String(20), nullable=True, server_default="technical"),
        )


def downgrade() -> None:
    op.drop_index("ix_tis_room_name", table_name="technical_interview_sessions")
    op.drop_constraint("uq_tis_room_name", "technical_interview_sessions", type_="unique")
    op.drop_column("technical_interview_sessions", "room_name")
    op.drop_column("technical_interview_sessions", "language")
    op.drop_column("technical_interview_sessions", "mode")

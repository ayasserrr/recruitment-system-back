"""add room_name, mode, language to hr_interview_sessions

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = "i3j4k5l6m7n8"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hr_interview_sessions",
        sa.Column("room_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "hr_interview_sessions",
        sa.Column("language", sa.String(10), nullable=True, server_default="en"),
    )
    op.add_column(
        "hr_interview_sessions",
        sa.Column("mode", sa.String(50), nullable=True, server_default="hr"),
    )
    op.create_unique_constraint("uq_hr_sessions_room_name", "hr_interview_sessions", ["room_name"])
    op.create_index("ix_hr_sessions_room_name", "hr_interview_sessions", ["room_name"])


def downgrade() -> None:
    op.drop_index("ix_hr_sessions_room_name", table_name="hr_interview_sessions")
    op.drop_constraint("uq_hr_sessions_room_name", "hr_interview_sessions", type_="unique")
    op.drop_column("hr_interview_sessions", "mode")
    op.drop_column("hr_interview_sessions", "language")
    op.drop_column("hr_interview_sessions", "room_name")

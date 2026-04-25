"""add_jr_knowledge_gaps

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - Creates jr_knowledge_gaps table
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "jr_knowledge_gaps" not in existing_tables:
        op.create_table(
            "jr_knowledge_gaps",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("jr_id", sa.Integer(), nullable=False),
            sa.Column("tool_name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=100), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["jr_id"],
                ["job_requisitions.requisition_id"],
                name="fk_knowledge_gap_jr",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_knowledge_gap_jr_id", "jr_knowledge_gaps", ["jr_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_gap_jr_id", table_name="jr_knowledge_gaps")
    op.drop_table("jr_knowledge_gaps")

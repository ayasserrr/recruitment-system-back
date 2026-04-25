"""add_assessment_leaderboard

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-25 00:00:00.000000

Changes:
  - Creates assessment_leaderboards table
  - Adds hr_report_path column to job_requisitions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    existing_jr_cols = [c["name"] for c in inspector.get_columns("job_requisitions")]

    if "assessment_leaderboards" not in existing_tables:
        op.create_table(
            "assessment_leaderboards",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("jr_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
            sa.Column("application_id", sa.Integer(), nullable=False),
            sa.Column("assessment_id", sa.Integer(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("final_score", sa.Numeric(precision=8, scale=4), nullable=True),
            sa.Column("segment", sa.String(length=100), nullable=True),
            sa.Column("reject", sa.Boolean(), nullable=True, default=False),
            sa.Column("reject_reason", sa.Text(), nullable=True),
            sa.Column("avg_depth", sa.Numeric(precision=8, scale=4), nullable=True),
            sa.Column(
                "per_question_detail",
                postgresql.JSON(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["jr_id"], ["job_requisitions.requisition_id"], name="fk_leaderboard_jr"
            ),
            sa.ForeignKeyConstraint(
                ["candidate_id"], ["candidates.candidate_id"], name="fk_leaderboard_candidate"
            ),
            sa.ForeignKeyConstraint(
                ["application_id"], ["applications.application_id"], name="fk_leaderboard_application"
            ),
            sa.ForeignKeyConstraint(
                ["assessment_id"],
                ["candidate_assessments.assessment_id"],
                name="fk_leaderboard_assessment",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_lb_indexes = {idx["name"] for idx in inspector.get_indexes("assessment_leaderboards")} \
        if "assessment_leaderboards" in existing_tables else set()

    if "ix_leaderboard_jr_id" not in existing_lb_indexes:
        op.create_index("ix_leaderboard_jr_id", "assessment_leaderboards", ["jr_id"])
    if "ix_leaderboard_candidate_id" not in existing_lb_indexes:
        op.create_index("ix_leaderboard_candidate_id", "assessment_leaderboards", ["candidate_id"])

    if "hr_report_path" not in existing_jr_cols:
        op.add_column(
            "job_requisitions",
            sa.Column("hr_report_path", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("job_requisitions", "hr_report_path")
    op.drop_index("ix_leaderboard_candidate_id", table_name="assessment_leaderboards")
    op.drop_index("ix_leaderboard_jr_id", table_name="assessment_leaderboards")
    op.drop_table("assessment_leaderboards")

"""add knowledge assessment tables

Revision ID: e4f5a6b7c8d9
Revises: d3c438a1516e
Create Date: 2026-04-25 00:00:00.000000

Creates:
  - generated_assessment_questions  (concept-grounded questions per JR)
  - assessment_question_sets        (ordered question sets per JR)

Adds to job_requisitions:
  - assessment_generated      BOOLEAN DEFAULT false
  - assessment_generated_at   TIMESTAMP nullable
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3c438a1516e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _existing_columns(bind, table: str) -> set[str]:
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _existing_tables(bind)

    if "generated_assessment_questions" not in tables:
        op.create_table(
            "generated_assessment_questions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("jr_id", sa.Integer(), nullable=False),
            sa.Column("concept_id", sa.Integer(), nullable=True),
            sa.Column("tool_id", sa.Integer(), nullable=False),
            sa.Column("tool_name", sa.String(length=100), nullable=False),
            sa.Column("level", sa.String(length=20), nullable=False),
            sa.Column("concept_name", sa.String(length=200), nullable=False),
            sa.Column("question_text", sa.Text(), nullable=False),
            sa.Column("question_type", sa.String(length=20), nullable=False, server_default="open_ended"),
            sa.Column("required_keywords", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.Column("template_question_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["jr_id"], ["job_requisitions.requisition_id"]),
            sa.ForeignKeyConstraint(
                ["template_question_id"],
                ["assessment_template_questions.question_id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if "assessment_question_sets" not in tables:
        op.create_table(
            "assessment_question_sets",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("jr_id", sa.Integer(), nullable=False),
            sa.Column("question_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["jr_id"], ["job_requisitions.requisition_id"]),
            sa.ForeignKeyConstraint(["question_id"], ["generated_assessment_questions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    jr_cols = _existing_columns(bind, "job_requisitions")
    if "assessment_generated" not in jr_cols:
        op.add_column(
            "job_requisitions",
            sa.Column("assessment_generated", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        )
    if "assessment_generated_at" not in jr_cols:
        op.add_column(
            "job_requisitions",
            sa.Column("assessment_generated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    jr_cols = _existing_columns(bind, "job_requisitions")
    if "assessment_generated_at" in jr_cols:
        op.drop_column("job_requisitions", "assessment_generated_at")
    if "assessment_generated" in jr_cols:
        op.drop_column("job_requisitions", "assessment_generated")

    tables = _existing_tables(bind)
    if "assessment_question_sets" in tables:
        op.drop_table("assessment_question_sets")
    if "generated_assessment_questions" in tables:
        op.drop_table("generated_assessment_questions")

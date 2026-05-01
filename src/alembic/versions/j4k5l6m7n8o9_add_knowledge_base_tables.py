"""add knowledge base tables

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8, h2i3j4k5l6m7
Create Date: 2026-04-29

Creates 4 tables that replace the static question_bank_service.py file:
  kb_topics              — topic taxonomy with self-referential hierarchy
  kb_questions           — question bank with difficulty, round_hint, JSONB metadata
  kb_question_embeddings — precomputed all-MiniLM-L6-v2 vectors (384-dim)
  jr_question_selections — per-candidate audit trail of which questions were selected
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, tuple] = ("i3j4k5l6m7n8", "h2i3j4k5l6m7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── kb_topics ─────────────────────────────────────────────────────────────
    op.create_table(
        "kb_topics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("kb_topics.id"), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_kb_topics_name"),
    )

    # ── kb_questions ──────────────────────────────────────────────────────────
    op.create_table(
        "kb_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("kb_topics.id"), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False, server_default="mid"),
        sa.Column("round_hint", sa.Integer(), nullable=True),
        sa.Column("context_tags", JSONB(), nullable=True),
        sa.Column("ideal_points", JSONB(), nullable=True),
        sa.Column("expert_terms", JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_questions_topic_id", "kb_questions", ["topic_id"])
    op.create_index("ix_kb_questions_is_active", "kb_questions", ["is_active"])

    # ── kb_question_embeddings ────────────────────────────────────────────────
    op.create_table(
        "kb_question_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("kb_questions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("model_id", sa.String(100), nullable=False, server_default="all-MiniLM-L6-v2"),
        sa.Column("embedding", JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_question_embeddings_question_id", "kb_question_embeddings", ["question_id"])

    # ── jr_question_selections ────────────────────────────────────────────────
    op.create_table(
        "jr_question_selections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "requisition_id",
            sa.Integer(),
            sa.ForeignKey("job_requisitions.requisition_id"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.application_id"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("kb_questions.id"),
            nullable=False,
        ),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("selected_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    op.create_index("ix_jr_question_selections_requisition_id", "jr_question_selections", ["requisition_id"])
    op.create_index("ix_jr_question_selections_application_id", "jr_question_selections", ["application_id"])


def downgrade() -> None:
    op.drop_table("jr_question_selections")
    op.drop_table("kb_question_embeddings")
    op.drop_table("kb_questions")
    op.drop_table("kb_topics")

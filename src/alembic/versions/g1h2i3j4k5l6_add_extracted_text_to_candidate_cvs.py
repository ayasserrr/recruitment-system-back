"""add extracted_text to candidate_cvs

Revision ID: g1h2i3j4k5l6
Revises: f7a8b9c0d1e2
Create Date: 2026-04-26 00:00:00.000000

Adds the extracted_text column to candidate_cvs.
This column stores the raw text extracted by the local fitz (PyMuPDF) parser
at CV upload time so the ranking pipeline never needs to re-parse the file.
"""
from alembic import op
import sqlalchemy as sa

revision = "g1h2i3j4k5l6"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_cvs",
        sa.Column("extracted_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_cvs", "extracted_text")

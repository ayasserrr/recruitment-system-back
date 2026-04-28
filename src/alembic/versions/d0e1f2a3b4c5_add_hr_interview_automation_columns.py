"""add_hr_interview_automation_columns

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-28

Adds 2 columns to job_requisitions to support the automated HR interview phase:
  - hr_interview_notified  (Boolean, default False) — set True after HR invitations sent
  - hr_interview_deadline  (DateTime, nullable)     — deadline for HR interviews
"""

from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_requisitions",
        sa.Column("hr_interview_notified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "job_requisitions",
        sa.Column("hr_interview_deadline", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_requisitions", "hr_interview_deadline")
    op.drop_column("job_requisitions", "hr_interview_notified")

"""add_shortlist_notified_to_job_requisitions

Revision ID: c5d6e7f8a9b0
Revises: see down_revision below
Create Date: 2026-04-12 00:00:00.000000

Adds a boolean guardrail column to job_requisitions so that the
post-ranking shortlist notification email is only dispatched once
per job, even if the ranking pipeline is re-run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
# Set to whichever revision `alembic current` reports for your DB.
# Most likely either 'a1b2c3d4e5f6' or 'merge_recruiter_into_company'.
down_revision: Union[str, None] = '462c07477351'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'job_requisitions',
        sa.Column(
            'shortlist_notified',
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('job_requisitions', 'shortlist_notified')

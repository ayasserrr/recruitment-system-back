"""merge multiple heads

Revision ID: 2d76b25cce55
Revises: a1b2c3d4e5f6, c5d6e7f8a9b0, merge_recruiter_into_company
Create Date: 2026-04-18 21:10:25.927833

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d76b25cce55'
down_revision: Union[str, None] = ('a1b2c3d4e5f6', 'c5d6e7f8a9b0', 'merge_recruiter_into_company')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

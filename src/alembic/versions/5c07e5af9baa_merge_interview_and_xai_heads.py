"""merge interview and xai heads

Revision ID: 5c07e5af9baa
Revises: a8b9c0d1e2f3, f1a2b3c4d5e6
Create Date: 2026-04-25 19:35:06.582003

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c07e5af9baa'
down_revision: Union[str, None] = ('a8b9c0d1e2f3', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

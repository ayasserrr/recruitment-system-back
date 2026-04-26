"""merge multiple heads

Revision ID: 47463f4ad0b2
Revises: b0c1d2e3f4a5, g1h2i3j4k5l6
Create Date: 2026-04-26 21:20:20.947448

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47463f4ad0b2'
down_revision: Union[str, None] = ('b0c1d2e3f4a5', 'g1h2i3j4k5l6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge multiple heads

Revision ID: 35752773b33a
Revises: 455716caa3ad, f4d2b7e2c6b1
Create Date: 2026-02-10 15:31:49.840774

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35752773b33a'
down_revision: Union[str, None] = ('455716caa3ad', 'f4d2b7e2c6b1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

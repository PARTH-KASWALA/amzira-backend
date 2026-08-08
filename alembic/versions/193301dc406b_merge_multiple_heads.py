"""merge multiple heads

Revision ID: 193301dc406b
Revises: 5f3c2b1a9d8e, f9c3d7e1b2a4
Create Date: 2026-03-30 19:48:39.327507

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '193301dc406b'
down_revision: Union[str, None] = ('5f3c2b1a9d8e', 'f9c3d7e1b2a4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

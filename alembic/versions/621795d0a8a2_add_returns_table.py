"""add_returns_table

Revision ID: 621795d0a8a2
Revises: 0e7dbc41d878
Create Date: 2026-02-02 13:09:28.447305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '621795d0a8a2'
down_revision: Union[str, None] = '0e7dbc41d878'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

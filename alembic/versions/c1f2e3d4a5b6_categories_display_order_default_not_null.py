"""Set categories.display_order default and NOT NULL

Revision ID: c1f2e3d4a5b6
Revises: b3f0a7c2d1e4
Create Date: 2026-02-17 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1f2e3d4a5b6"
down_revision: Union[str, None] = "b3f0a7c2d1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE categories SET display_order = 0 WHERE display_order IS NULL")
    op.alter_column(
        "categories",
        "display_order",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "categories",
        "display_order",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )

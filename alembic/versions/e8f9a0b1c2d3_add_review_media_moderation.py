"""add moderation state for customer review photos

Revision ID: e8f9a0b1c2d3
Revises: c2d3e4f5a6b7
Create Date: 2026-09-06 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing evidence was already added through the admin-only import path;
    # keep it visible while all future direct uploads start in moderation.
    op.add_column(
        "review_media",
        sa.Column("is_published", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("review_media", "is_published")

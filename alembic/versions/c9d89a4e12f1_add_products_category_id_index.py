"""Add single-column index on products.category_id

Revision ID: c9d89a4e12f1
Revises: a1f4b5c7d9e0
Create Date: 2026-02-07 12:10:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9d89a4e12f1"
down_revision: Union[str, None] = "a1f4b5c7d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_products_category_id", "products", ["category_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_category_id", table_name="products")

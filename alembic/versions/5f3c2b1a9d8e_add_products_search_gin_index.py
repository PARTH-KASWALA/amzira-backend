"""Add PostgreSQL full-text search GIN index for products

Revision ID: 5f3c2b1a9d8e
Revises: 455716caa3ad, 7d4b2c1f0a9e, c1f2e3d4a5b6, f4d2b7e2c6b1
Create Date: 2026-03-27 12:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "5f3c2b1a9d8e"
down_revision: Union[str, Sequence[str], None] = (
    "455716caa3ad",
    "7d4b2c1f0a9e",
    "c1f2e3d4a5b6",
    "f4d2b7e2c6b1",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(index.get("name") == index_name for index in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = inspect(bind)
    index_name = "ix_products_search_tsv_gin"
    if _has_index(inspector, "products", index_name):
        return

    op.execute(
        """
        CREATE INDEX ix_products_search_tsv_gin
        ON products
        USING GIN (
            to_tsvector(
                'simple',
                coalesce(name, '') || ' ' || coalesce(description, '')
            )
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_products_search_tsv_gin")

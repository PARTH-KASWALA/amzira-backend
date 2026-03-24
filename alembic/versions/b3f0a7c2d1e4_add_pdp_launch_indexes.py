"""Add PDP launch indexes

Revision ID: b3f0a7c2d1e4
Revises: 9f2c7b1a6e3d
Create Date: 2026-02-16 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b3f0a7c2d1e4"
down_revision: Union[str, None] = "9f2c7b1a6e3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(i.get("name") == index_name for i in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _has_index(inspector, "products", "ix_products_category_active"):
        op.create_index(
            "ix_products_category_active",
            "products",
            ["category_id", "is_active"],
            unique=False,
        )

    if not _has_index(inspector, "cart_items", "ix_cart_items_user_id"):
        op.create_index(
            "ix_cart_items_user_id",
            "cart_items",
            ["user_id"],
            unique=False,
        )

    if not _has_index(inspector, "orders", "ix_orders_user_status"):
        op.create_index(
            "ix_orders_user_status",
            "orders",
            ["user_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _has_index(inspector, "orders", "ix_orders_user_status"):
        op.drop_index("ix_orders_user_status", table_name="orders")

    if _has_index(inspector, "cart_items", "ix_cart_items_user_id"):
        op.drop_index("ix_cart_items_user_id", table_name="cart_items")

    if _has_index(inspector, "products", "ix_products_category_active"):
        op.drop_index("ix_products_category_active", table_name="products")

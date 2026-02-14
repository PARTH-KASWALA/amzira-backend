"""Hardening indexes and order expiry fields

Revision ID: a1f4b5c7d9e0
Revises: 621795d0a8a2
Create Date: 2026-02-06 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "a1f4b5c7d9e0"
down_revision: Union[str, None] = "621795d0a8a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("stock_deducted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    op.create_index("ix_orders_user_created_at", "orders", ["user_id", "created_at"], unique=False)
    op.create_index("ix_orders_status_created_at", "orders", ["status", "created_at"], unique=False)
    if "cart_items" in existing_tables:
        op.create_index("ix_cart_items_user_id", "cart_items", ["user_id"], unique=False)
    if "product_variants" in existing_tables:
        op.create_index(
            "ix_product_variants_product_id",
            "product_variants",
            ["product_id"],
            unique=False,
        )
    if "order_items" in existing_tables:
        op.create_index("ix_order_items_order_id", "order_items", ["order_id"], unique=False)

    # Safe migration: only create reviews index if table exists
    if "reviews" in existing_tables:
        op.create_index(
            "ix_reviews_product_created_at",
            "reviews",
            ["product_id", "created_at"],
            unique=False,
        )
    # Safe migration: only create wishlist index if table exists
    if "wishlist" in existing_tables:
        op.create_index("ix_wishlist_user_id", "wishlist", ["user_id"], unique=False)

    op.alter_column("orders", "stock_deducted", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    def _has_index(table_name: str, index_name: str) -> bool:
        try:
            idx = inspector.get_indexes(table_name)
        except Exception:
            return False
        return any(i.get("name") == index_name for i in idx)

    if "wishlist" in existing_tables and _has_index("wishlist", "ix_wishlist_user_id"):
        op.drop_index("ix_wishlist_user_id", table_name="wishlist")

    # Safe migration: only drop reviews index if table exists
    if "reviews" in existing_tables and _has_index("reviews", "ix_reviews_product_created_at"):
        op.drop_index("ix_reviews_product_created_at", table_name="reviews")

    if "order_items" in existing_tables and _has_index("order_items", "ix_order_items_order_id"):
        op.drop_index("ix_order_items_order_id", table_name="order_items")
    if "product_variants" in existing_tables and _has_index(
        "product_variants", "ix_product_variants_product_id"
    ):
        op.drop_index("ix_product_variants_product_id", table_name="product_variants")
    if "cart_items" in existing_tables and _has_index("cart_items", "ix_cart_items_user_id"):
        op.drop_index("ix_cart_items_user_id", table_name="cart_items")

    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_index("ix_orders_user_created_at", table_name="orders")
    op.drop_column("orders", "stock_deducted")
    op.drop_column("orders", "expires_at")

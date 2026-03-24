"""Refactor inventory to variant-level stock

Revision ID: 9f2c7b1a6e3d
Revises: a1f4b5c7d9e0
Create Date: 2026-02-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "9f2c7b1a6e3d"
down_revision: Union[str, None] = "35752773b33a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(i.get("name") == index_name for i in indexes)


def _get_fk_name(inspector, table_name: str, constrained_cols, referred_table: str):
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("referred_table") == referred_table and fk.get("constrained_columns") == constrained_cols:
            return fk.get("name")
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    op.drop_column("products", "total_stock")

    if not _has_index(inspector, "product_variants", "ix_product_variants_sku"):
        op.execute(
            "CREATE UNIQUE INDEX ix_product_variants_sku ON product_variants (sku)"
        )

    op.create_check_constraint(
        "ck_product_variants_stock_non_negative",
        "product_variants",
        "stock_quantity >= 0",
    )

    if not _has_index(inspector, "product_variants", "ix_product_variants_product_id"):
        op.create_index(
            "ix_product_variants_product_id",
            "product_variants",
            ["product_id"],
        )

    if not _has_index(inspector, "product_images", "ix_product_images_product_id"):
        op.create_index(
            "ix_product_images_product_id",
            "product_images",
            ["product_id"],
        )

    fk_name = _get_fk_name(inspector, "product_variants", ["product_id"], "products")
    if fk_name:
        op.drop_constraint(fk_name, "product_variants", type_="foreignkey")

    op.create_foreign_key(
        "fk_product_variants_product_id_products",
        "product_variants",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    op.drop_constraint(
        "fk_product_variants_product_id_products",
        "product_variants",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_product_variants_product_id_products",
        "product_variants",
        "products",
        ["product_id"],
        ["id"],
    )

    if _has_index(inspector, "product_images", "ix_product_images_product_id"):
        op.drop_index("ix_product_images_product_id", table_name="product_images")

    if _has_index(inspector, "product_variants", "ix_product_variants_product_id"):
        op.drop_index("ix_product_variants_product_id", table_name="product_variants")

    if _has_index(inspector, "product_variants", "ix_product_variants_sku"):
        op.execute("DROP INDEX ix_product_variants_sku")

    op.drop_constraint(
        "ck_product_variants_stock_non_negative",
        "product_variants",
        type_="check",
    )

    op.add_column(
        "products",
        sa.Column("total_stock", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("products", "total_stock", server_default=None)

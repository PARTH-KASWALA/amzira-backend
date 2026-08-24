"""reconcile schema metadata and seed launch occasions

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-08-12 06:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    required_indexes = {
        "coupons": ("ix_coupons_id", ["id"], False),
        "coupon_usages": ("ix_coupon_usages_id", ["id"], False),
        "order_status_history": ("ix_order_status_history_id", ["id"], False),
        "orders": ("ix_orders_expires_at", ["expires_at"], False),
        "wishlists": ("ix_wishlists_id", ["id"], False),
    }
    for table_name, (index_name, columns, unique) in required_indexes.items():
        if index_name not in _index_names(inspector, table_name):
            op.create_index(index_name, table_name, columns, unique=unique)

    coupon_unique_names = {
        item.get("name") for item in inspector.get_unique_constraints("coupons")
    }
    if "coupons_code_key" in coupon_unique_names:
        op.drop_constraint("coupons_code_key", "coupons", type_="unique")

    product_image_fks = inspector.get_foreign_keys("product_images")
    product_fk = next(
        (item for item in product_image_fks if item.get("constrained_columns") == ["product_id"]),
        None,
    )
    if product_fk and str(product_fk.get("options", {}).get("ondelete", "")).upper() != "CASCADE":
        if product_fk.get("name"):
            op.drop_constraint(product_fk["name"], "product_images", type_="foreignkey")
        op.create_foreign_key(
            "fk_product_images_product_id",
            "product_images",
            "products",
            ["product_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.execute(
        """
        INSERT INTO occasions (name, slug)
        VALUES
            ('Festival', 'festival'),
            ('Wedding', 'wedding'),
            ('Temple Ceremony', 'temple-ceremony'),
            ('Birthday', 'birthday')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM occasions WHERE slug IN ('festival', 'wedding', 'temple-ceremony', 'birthday')"
    )
    for table_name, index_name in (
        ("wishlists", "ix_wishlists_id"),
        ("orders", "ix_orders_expires_at"),
        ("order_status_history", "ix_order_status_history_id"),
        ("coupon_usages", "ix_coupon_usages_id"),
        ("coupons", "ix_coupons_id"),
    ):
        op.drop_index(index_name, table_name=table_name)

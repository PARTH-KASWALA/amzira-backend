"""add checkout coupon reservations

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-08-12 05:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "coupons" not in tables:
        op.create_table(
            "coupons",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(50), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "discount_type",
                sa.Enum("PERCENTAGE", "FIXED", name="discounttype"),
                nullable=False,
            ),
            sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
            sa.Column("min_order_value", sa.Numeric(10, 2), server_default="0", nullable=False),
            sa.Column("max_discount", sa.Numeric(10, 2), nullable=True),
            sa.Column("usage_limit", sa.Integer(), nullable=True),
            sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("reserved_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("per_user_limit", sa.Integer(), server_default="1", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("expiry_date", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)
    else:
        for column_name in ("discount_value", "min_order_value", "max_discount"):
            op.alter_column(
                "coupons",
                column_name,
                existing_type=sa.Float(),
                type_=sa.Numeric(10, 2),
                existing_nullable=column_name == "max_discount",
                postgresql_using=f"{column_name}::numeric(10,2)",
            )
        if "reserved_count" not in {column["name"] for column in inspector.get_columns("coupons")}:
            op.add_column(
                "coupons",
                sa.Column("reserved_count", sa.Integer(), server_default="0", nullable=False),
            )

    if "coupon_usages" not in tables:
        op.create_table(
            "coupon_usages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("used_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("order_id", name="uq_coupon_usages_order_id"),
        )
    else:
        op.execute(
            """
            DELETE FROM coupon_usages duplicate
            USING coupon_usages original
            WHERE duplicate.order_id = original.order_id
              AND duplicate.id > original.id
            """
        )
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("coupon_usages")}
        if "uq_coupon_usages_order_id" not in unique_names:
            op.create_unique_constraint("uq_coupon_usages_order_id", "coupon_usages", ["order_id"])

    if "order_status_history" not in tables:
        op.create_table(
            "order_status_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("old_status", sa.String(50), nullable=True),
            sa.Column("new_status", sa.String(50), nullable=False),
            sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_order_status_history_order_id", "order_status_history", ["order_id"])

    if "reviews" not in tables:
        op.create_table(
            "reviews",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("verified_purchase", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "product_id", name="unique_user_product_review"),
            sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_reviews_rating_range"),
        )
        op.create_index("ix_reviews_product_id", "reviews", ["product_id"])

    if "wishlists" not in tables:
        op.create_table(
            "wishlists",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "product_id", name="unique_user_product_wishlist"),
        )
        op.create_index("ix_wishlists_user_id", "wishlists", ["user_id"])

    op.add_column(
        "checkout_payment_intents",
        sa.Column("discount_amount", sa.Numeric(10, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "checkout_payment_intents",
        sa.Column("coupon_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "checkout_payment_intents",
        sa.Column("coupon_code", sa.String(50), nullable=True),
    )
    op.add_column(
        "checkout_payment_intents",
        sa.Column("coupon_reserved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_foreign_key(
        "fk_checkout_payment_intents_coupon_id",
        "checkout_payment_intents",
        "coupons",
        ["coupon_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_checkout_payment_intents_coupon_id",
        "checkout_payment_intents",
        ["coupon_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_checkout_payment_intents_coupon_id", table_name="checkout_payment_intents")
    op.drop_constraint(
        "fk_checkout_payment_intents_coupon_id",
        "checkout_payment_intents",
        type_="foreignkey",
    )
    op.drop_column("checkout_payment_intents", "coupon_reserved")
    op.drop_column("checkout_payment_intents", "coupon_code")
    op.drop_column("checkout_payment_intents", "coupon_id")
    op.drop_column("checkout_payment_intents", "discount_amount")
    op.drop_column("coupons", "reserved_count")
    for column_name in ("max_discount", "min_order_value", "discount_value"):
        op.alter_column(
            "coupons",
            column_name,
            existing_type=sa.Numeric(10, 2),
            type_=sa.Float(),
            existing_nullable=column_name == "max_discount",
            postgresql_using=f"{column_name}::double precision",
        )
    op.drop_constraint("uq_coupon_usages_order_id", "coupon_usages", type_="unique")

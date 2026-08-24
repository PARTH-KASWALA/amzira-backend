"""add transaction integrity and refund state

Revision ID: a2b3c4d5e6f7
Revises: 193301dc406b
Create Date: 2026-08-12 01:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "193301dc406b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE returnstatus ADD VALUE IF NOT EXISTS 'REFUND_PENDING'")
    op.execute("ALTER TYPE returnstatus ADD VALUE IF NOT EXISTS 'REFUND_FAILED'")

    op.alter_column(
        "products",
        "base_price",
        existing_type=sa.Float(),
        type_=sa.Numeric(10, 2),
        existing_nullable=False,
        postgresql_using="base_price::numeric(10,2)",
    )
    op.alter_column(
        "products",
        "sale_price",
        existing_type=sa.Float(),
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
        postgresql_using="sale_price::numeric(10,2)",
    )
    op.alter_column(
        "product_variants",
        "additional_price",
        existing_type=sa.Float(),
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
        postgresql_using="additional_price::numeric(10,2)",
    )

    for column_name in ("amount", "subtotal", "tax_amount", "total_amount"):
        op.alter_column(
            "checkout_payment_intents",
            column_name,
            existing_type=sa.Float(),
            type_=sa.Numeric(10, 2),
            existing_nullable=False,
            postgresql_using=f"{column_name}::numeric(10,2)",
        )

    op.add_column(
        "checkout_payment_intents",
        sa.Column("shipping_amount", sa.Numeric(10, 2), server_default="0", nullable=False),
    )
    op.add_column("checkout_payment_intents", sa.Column("cart_fingerprint", sa.String(64), nullable=True))
    op.add_column(
        "checkout_payment_intents",
        sa.Column("stock_reserved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("checkout_payment_intents", sa.Column("reservation_released_at", sa.DateTime(), nullable=True))
    op.add_column("checkout_payment_intents", sa.Column("reservation_consumed_at", sa.DateTime(), nullable=True))
    op.add_column("checkout_payment_intents", sa.Column("failure_reason", sa.String(255), nullable=True))
    op.add_column("checkout_payment_intents", sa.Column("recovery_refund_id", sa.String(100), nullable=True))
    op.add_column("checkout_payment_intents", sa.Column("recovery_refund_status", sa.String(30), nullable=True))
    op.add_column("checkout_payment_intents", sa.Column("recovery_gateway_response", sa.Text(), nullable=True))
    op.create_index(
        "ix_checkout_payment_intents_cart_fingerprint",
        "checkout_payment_intents",
        ["cart_fingerprint"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_checkout_payment_intents_recovery_refund_id",
        "checkout_payment_intents",
        ["recovery_refund_id"],
    )

    op.add_column(
        "payments",
        sa.Column("refunded_amount", sa.Numeric(10, 2), server_default="0", nullable=False),
    )
    op.add_column("payments", sa.Column("refunded_at", sa.DateTime(), nullable=True))
    op.create_index(
        "uq_payments_razorpay_payment_id_not_null",
        "payments",
        ["razorpay_payment_id"],
        unique=True,
        postgresql_where=sa.text("razorpay_payment_id IS NOT NULL"),
    )

    op.add_column("return_requests", sa.Column("refund_error", sa.String(500), nullable=True))
    op.add_column("return_requests", sa.Column("refund_gateway_response", sa.Text(), nullable=True))
    op.add_column(
        "return_requests",
        sa.Column("inventory_restocked", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "uq_return_requests_refund_transaction_id_not_null",
        "return_requests",
        ["refund_transaction_id"],
        unique=True,
        postgresql_where=sa.text("refund_transaction_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_return_requests_refund_transaction_id_not_null", table_name="return_requests")
    op.drop_column("return_requests", "inventory_restocked")
    op.drop_column("return_requests", "refund_gateway_response")
    op.drop_column("return_requests", "refund_error")

    op.drop_index("uq_payments_razorpay_payment_id_not_null", table_name="payments")
    op.drop_column("payments", "refunded_at")
    op.drop_column("payments", "refunded_amount")

    op.drop_constraint(
        "uq_checkout_payment_intents_recovery_refund_id",
        "checkout_payment_intents",
        type_="unique",
    )
    op.drop_index("ix_checkout_payment_intents_cart_fingerprint", table_name="checkout_payment_intents")
    for column_name in (
        "recovery_gateway_response",
        "recovery_refund_status",
        "recovery_refund_id",
        "failure_reason",
        "reservation_consumed_at",
        "reservation_released_at",
        "stock_reserved",
        "cart_fingerprint",
        "shipping_amount",
    ):
        op.drop_column("checkout_payment_intents", column_name)

    for column_name in ("total_amount", "tax_amount", "subtotal", "amount"):
        op.alter_column(
            "checkout_payment_intents",
            column_name,
            existing_type=sa.Numeric(10, 2),
            type_=sa.Float(),
            existing_nullable=False,
            postgresql_using=f"{column_name}::double precision",
        )

    op.alter_column(
        "product_variants",
        "additional_price",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="additional_price::double precision",
    )
    op.alter_column(
        "products",
        "sale_price",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="sale_price::double precision",
    )
    op.alter_column(
        "products",
        "base_price",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="base_price::double precision",
    )

"""add checkout payment intents and tracking aliases

Revision ID: f9c3d7e1b2a4
Revises: 7d4b2c1f0a9e
Create Date: 2026-03-30 19:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "f9c3d7e1b2a4"
down_revision: Union[str, None] = "7d4b2c1f0a9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


checkoutpaymentintentstatus = postgresql.ENUM(
    "PENDING",
    "SUCCESS",
    "FAILED",
    "EXPIRED",
    name="checkoutpaymentintentstatus",
    create_type=False,
)


def _has_table(table_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    checkoutpaymentintentstatus.create(op.get_bind(), checkfirst=True)
    if not _has_table("checkout_payment_intents"):
        op.create_table(
            "checkout_payment_intents",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("address_id", sa.Integer(), nullable=False),
            sa.Column("created_order_id", sa.Integer(), nullable=True),
            sa.Column("razorpay_order_id", sa.String(length=100), nullable=False),
            sa.Column("razorpay_payment_id", sa.String(length=100), nullable=True),
            sa.Column("razorpay_signature", sa.String(length=200), nullable=True),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("subtotal", sa.Float(), nullable=False),
            sa.Column("tax_amount", sa.Float(), nullable=False),
            sa.Column("total_amount", sa.Float(), nullable=False),
            sa.Column("status", checkoutpaymentintentstatus, nullable=False),
            sa.Column("cart_snapshot", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["address_id"], ["addresses.id"]),
            sa.ForeignKeyConstraint(["created_order_id"], ["orders.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("razorpay_order_id"),
        )

    if _has_table("checkout_payment_intents"):
        for index_name, columns in (
            ("ix_checkout_payment_intents_id", ["id"]),
            ("ix_checkout_payment_intents_user_id", ["user_id"]),
            ("ix_checkout_payment_intents_created_order_id", ["created_order_id"]),
            ("ix_checkout_payment_intents_razorpay_order_id", ["razorpay_order_id"]),
            ("ix_checkout_payment_intents_razorpay_payment_id", ["razorpay_payment_id"]),
            ("ix_checkout_payment_intents_status", ["status"]),
            ("ix_checkout_payment_intents_expires_at", ["expires_at"]),
        ):
            if not _has_index("checkout_payment_intents", op.f(index_name)):
                op.create_index(op.f(index_name), "checkout_payment_intents", columns, unique=False)

    for column_name, column_type in (
        ("tracking_id", sa.String(length=100)),
        ("courier_status", sa.String(length=100)),
        ("delivery_date", sa.DateTime()),
    ):
        if not _has_column("orders", column_name):
            op.add_column("orders", sa.Column(column_name, column_type, nullable=True))
    if not _has_index("orders", op.f("ix_orders_tracking_id")):
        op.create_index(op.f("ix_orders_tracking_id"), "orders", ["tracking_id"], unique=False)


def downgrade() -> None:
    if _has_index("orders", op.f("ix_orders_tracking_id")):
        op.drop_index(op.f("ix_orders_tracking_id"), table_name="orders")
    for column_name in ("delivery_date", "courier_status", "tracking_id"):
        if _has_column("orders", column_name):
            op.drop_column("orders", column_name)

    if _has_table("checkout_payment_intents"):
        for index_name in (
            "ix_checkout_payment_intents_expires_at",
            "ix_checkout_payment_intents_status",
            "ix_checkout_payment_intents_razorpay_payment_id",
            "ix_checkout_payment_intents_razorpay_order_id",
            "ix_checkout_payment_intents_created_order_id",
            "ix_checkout_payment_intents_user_id",
            "ix_checkout_payment_intents_id",
        ):
            if _has_index("checkout_payment_intents", op.f(index_name)):
                op.drop_index(op.f(index_name), table_name="checkout_payment_intents")
        op.drop_table("checkout_payment_intents")
    checkoutpaymentintentstatus.drop(op.get_bind(), checkfirst=True)

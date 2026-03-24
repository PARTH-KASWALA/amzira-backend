"""add production return window and money precision

Revision ID: e1a2b3c4d5f6
Revises: 621795d0a8a2
Create Date: 2026-03-23 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, None] = "621795d0a8a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("return_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "return_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_applicable",
        ),
    )

    op.alter_column("orders", "subtotal", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=False)
    op.alter_column("orders", "tax_amount", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=True)
    op.alter_column("orders", "shipping_charge", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=True)
    op.alter_column("orders", "discount_amount", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=True)
    op.alter_column("orders", "total_amount", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=False)
    op.alter_column("order_items", "unit_price", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=False)
    op.alter_column("order_items", "total_price", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=False)
    op.alter_column("payments", "amount", existing_type=sa.Float(), type_=sa.Numeric(10, 2), existing_nullable=False)

    op.create_table(
        "return_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "SIZE_ISSUE",
                "DAMAGED",
                "WRONG_ITEM",
                "NOT_AS_DESCRIBED",
                "QUALITY_ISSUE",
                "OTHER",
                name="returnreason",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "REQUESTED",
                "APPROVED",
                "REJECTED",
                "PICKED_UP",
                "REFUNDED",
                name="returnstatus",
            ),
            nullable=False,
        ),
        sa.Column("refund_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("refund_method", sa.String(length=50), nullable=True),
        sa.Column("refund_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_item_id", name="uq_return_requests_order_item_id"),
    )
    op.create_index(op.f("ix_return_requests_id"), "return_requests", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_return_requests_id"), table_name="return_requests")
    op.drop_table("return_requests")

    op.alter_column("payments", "amount", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=False)
    op.alter_column("order_items", "total_price", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=False)
    op.alter_column("order_items", "unit_price", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=False)
    op.alter_column("orders", "total_amount", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=False)
    op.alter_column("orders", "discount_amount", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=True)
    op.alter_column("orders", "shipping_charge", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=True)
    op.alter_column("orders", "tax_amount", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=True)
    op.alter_column("orders", "subtotal", existing_type=sa.Numeric(10, 2), type_=sa.Float(), existing_nullable=False)

    op.drop_column("orders", "return_status")
    op.drop_column("orders", "return_deadline")
    op.drop_column("orders", "delivered_at")

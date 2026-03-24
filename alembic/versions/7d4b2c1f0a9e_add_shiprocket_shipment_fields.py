"""add shiprocket shipment fields

Revision ID: 7d4b2c1f0a9e
Revises: e1a2b3c4d5f6
Create Date: 2026-03-24 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d4b2c1f0a9e"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("courier_name", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("shiprocket_order_id", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("shipment_id", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("awb_code", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("tracking_url", sa.String(length=500), nullable=True))
    op.add_column("orders", sa.Column("current_location", sa.String(length=255), nullable=True))
    op.add_column("orders", sa.Column("shiprocket_last_status", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("pickup_scheduled_at", sa.DateTime(), nullable=True))
    op.add_column("return_requests", sa.Column("shiprocket_return_order_id", sa.String(length=100), nullable=True))
    op.add_column("return_requests", sa.Column("shiprocket_return_shipment_id", sa.String(length=100), nullable=True))
    op.add_column("return_requests", sa.Column("return_awb_code", sa.String(length=100), nullable=True))
    op.add_column("return_requests", sa.Column("return_tracking_url", sa.String(length=500), nullable=True))
    op.add_column("return_requests", sa.Column("return_courier_name", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_orders_shipment_id"), "orders", ["shipment_id"], unique=False)
    op.create_index(op.f("ix_orders_shiprocket_order_id"), "orders", ["shiprocket_order_id"], unique=False)
    op.create_index(op.f("ix_orders_awb_code"), "orders", ["awb_code"], unique=False)
    op.create_index(op.f("ix_return_requests_shiprocket_return_order_id"), "return_requests", ["shiprocket_return_order_id"], unique=False)
    op.create_index(op.f("ix_return_requests_shiprocket_return_shipment_id"), "return_requests", ["shiprocket_return_shipment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_return_requests_shiprocket_return_shipment_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_shiprocket_return_order_id"), table_name="return_requests")
    op.drop_index(op.f("ix_orders_awb_code"), table_name="orders")
    op.drop_index(op.f("ix_orders_shiprocket_order_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_shipment_id"), table_name="orders")
    op.drop_column("return_requests", "return_courier_name")
    op.drop_column("return_requests", "return_tracking_url")
    op.drop_column("return_requests", "return_awb_code")
    op.drop_column("return_requests", "shiprocket_return_shipment_id")
    op.drop_column("return_requests", "shiprocket_return_order_id")
    op.drop_column("orders", "pickup_scheduled_at")
    op.drop_column("orders", "shiprocket_last_status")
    op.drop_column("orders", "current_location")
    op.drop_column("orders", "tracking_url")
    op.drop_column("orders", "awb_code")
    op.drop_column("orders", "shipment_id")
    op.drop_column("orders", "shiprocket_order_id")
    op.drop_column("orders", "courier_name")

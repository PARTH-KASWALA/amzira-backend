"""add shiprocket shipment fields

Revision ID: 7d4b2c1f0a9e
Revises: e1a2b3c4d5f6
Create Date: 2026-03-24 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "7d4b2c1f0a9e"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    order_columns = (
        ("courier_name", sa.String(length=100)),
        ("shiprocket_order_id", sa.String(length=100)),
        ("shipment_id", sa.String(length=100)),
        ("awb_code", sa.String(length=100)),
        ("tracking_url", sa.String(length=500)),
        ("current_location", sa.String(length=255)),
        ("shiprocket_last_status", sa.String(length=100)),
        ("pickup_scheduled_at", sa.DateTime()),
    )
    for column_name, column_type in order_columns:
        if not _has_column("orders", column_name):
            op.add_column("orders", sa.Column(column_name, column_type, nullable=True))

    if _has_table("return_requests"):
        return_columns = (
            ("shiprocket_return_order_id", sa.String(length=100)),
            ("shiprocket_return_shipment_id", sa.String(length=100)),
            ("return_awb_code", sa.String(length=100)),
            ("return_tracking_url", sa.String(length=500)),
            ("return_courier_name", sa.String(length=100)),
        )
        for column_name, column_type in return_columns:
            if not _has_column("return_requests", column_name):
                op.add_column("return_requests", sa.Column(column_name, column_type, nullable=True))

    order_indexes = (
        ("ix_orders_shipment_id", ["shipment_id"]),
        ("ix_orders_shiprocket_order_id", ["shiprocket_order_id"]),
        ("ix_orders_awb_code", ["awb_code"]),
    )
    for index_name, columns in order_indexes:
        if not _has_index("orders", op.f(index_name)):
            op.create_index(op.f(index_name), "orders", columns, unique=False)

    if _has_table("return_requests"):
        return_indexes = (
            ("ix_return_requests_shiprocket_return_order_id", ["shiprocket_return_order_id"]),
            ("ix_return_requests_shiprocket_return_shipment_id", ["shiprocket_return_shipment_id"]),
        )
        for index_name, columns in return_indexes:
            if not _has_index("return_requests", op.f(index_name)):
                op.create_index(op.f(index_name), "return_requests", columns, unique=False)


def downgrade() -> None:
    if _has_table("return_requests") and _has_index("return_requests", op.f("ix_return_requests_shiprocket_return_shipment_id")):
        op.drop_index(op.f("ix_return_requests_shiprocket_return_shipment_id"), table_name="return_requests")
    if _has_table("return_requests") and _has_index("return_requests", op.f("ix_return_requests_shiprocket_return_order_id")):
        op.drop_index(op.f("ix_return_requests_shiprocket_return_order_id"), table_name="return_requests")
    if _has_index("orders", op.f("ix_orders_awb_code")):
        op.drop_index(op.f("ix_orders_awb_code"), table_name="orders")
    if _has_index("orders", op.f("ix_orders_shiprocket_order_id")):
        op.drop_index(op.f("ix_orders_shiprocket_order_id"), table_name="orders")
    if _has_index("orders", op.f("ix_orders_shipment_id")):
        op.drop_index(op.f("ix_orders_shipment_id"), table_name="orders")

    if _has_table("return_requests"):
        for column_name in (
            "return_courier_name",
            "return_tracking_url",
            "return_awb_code",
            "shiprocket_return_shipment_id",
            "shiprocket_return_order_id",
        ):
            if _has_column("return_requests", column_name):
                op.drop_column("return_requests", column_name)

    for column_name in (
        "pickup_scheduled_at",
        "shiprocket_last_status",
        "current_location",
        "tracking_url",
        "awb_code",
        "shipment_id",
        "shiprocket_order_id",
        "courier_name",
    ):
        if _has_column("orders", column_name):
            op.drop_column("orders", column_name)

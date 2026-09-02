"""Normalize order status enum values to lowercase.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-09-02 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LOWERCASE_STATUSES = (
    "placed",
    "pending",
    "confirmed",
    "processing",
    "shipped",
    "out_for_delivery",
    "delivered",
    "return_requested",
    "returned",
    "cancelled",
    "refunded",
)


def _quoted_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    values = _quoted_values(LOWERCASE_STATUSES)
    op.execute(f"CREATE TYPE orderstatus_normalized AS ENUM ({values})")
    op.execute(
        """
        ALTER TABLE orders
        ALTER COLUMN status TYPE orderstatus_normalized
        USING lower(status::text)::orderstatus_normalized
        """
    )
    op.execute("DROP TYPE orderstatus")
    op.execute("ALTER TYPE orderstatus_normalized RENAME TO orderstatus")


def downgrade() -> None:
    uppercase_statuses = tuple(status.upper() for status in LOWERCASE_STATUSES)
    values = _quoted_values(uppercase_statuses)
    op.execute(f"CREATE TYPE orderstatus_legacy AS ENUM ({values})")
    op.execute(
        """
        ALTER TABLE orders
        ALTER COLUMN status TYPE orderstatus_legacy
        USING upper(status::text)::orderstatus_legacy
        """
    )
    op.execute("DROP TYPE orderstatus")
    op.execute("ALTER TYPE orderstatus_legacy RENAME TO orderstatus")

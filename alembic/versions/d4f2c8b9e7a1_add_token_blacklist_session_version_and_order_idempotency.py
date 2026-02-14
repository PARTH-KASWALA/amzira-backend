"""Add token blacklist, user session version, and order idempotency key

Revision ID: d4f2c8b9e7a1
Revises: c9d89a4e12f1
Create Date: 2026-02-08 18:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4f2c8b9e7a1"
down_revision: Union[str, None] = "c9d89a4e12f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_token_blacklist_id"), "token_blacklist", ["id"], unique=False)
    op.create_index(op.f("ix_token_blacklist_jti"), "token_blacklist", ["jti"], unique=True)
    op.create_index(op.f("ix_token_blacklist_user_id"), "token_blacklist", ["user_id"], unique=False)
    op.create_index(op.f("ix_token_blacklist_expires_at"), "token_blacklist", ["expires_at"], unique=False)

    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("users", "session_version", server_default=None)

    op.add_column("orders", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_orders_idempotency_key"), "orders", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_idempotency_key"), table_name="orders")
    op.drop_column("orders", "idempotency_key")

    op.drop_column("users", "session_version")

    op.drop_index(op.f("ix_token_blacklist_expires_at"), table_name="token_blacklist")
    op.drop_index(op.f("ix_token_blacklist_user_id"), table_name="token_blacklist")
    op.drop_index(op.f("ix_token_blacklist_jti"), table_name="token_blacklist")
    op.drop_index(op.f("ix_token_blacklist_id"), table_name="token_blacklist")
    op.drop_table("token_blacklist")

"""add product confidence facts and moderated review evidence

Revision ID: c2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-09-06 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("is_most_loved", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("products", sa.Column("lining", sa.String(length=200), nullable=True))
    op.add_column("products", sa.Column("included_pieces", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("age_recommendation", sa.String(length=100), nullable=True))
    op.add_column("products", sa.Column("fit_note", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("dispatch_days_min", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("dispatch_days_max", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("is_exchange_eligible", sa.Boolean(), nullable=True))
    op.add_column("products", sa.Column("is_return_eligible", sa.Boolean(), nullable=True))
    op.add_column("products", sa.Column("return_window_hours", sa.Integer(), nullable=True))
    op.add_column("product_variants", sa.Column("measurements", sa.JSON(), nullable=True))

    op.alter_column("reviews", "user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("reviews", sa.Column("marketplace_verified_purchase", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("reviews", sa.Column("source", sa.String(length=32), server_default="amzira", nullable=False))
    op.add_column("reviews", sa.Column("source_review_id", sa.String(length=128), nullable=True))
    op.add_column("reviews", sa.Column("source_product_id", sa.String(length=128), nullable=True))
    op.add_column("reviews", sa.Column("source_listing_url", sa.String(length=500), nullable=True))
    op.add_column("reviews", sa.Column("reviewer_name", sa.String(length=120), nullable=True))
    op.add_column("reviews", sa.Column("is_published", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.create_unique_constraint("uq_reviews_source_review_id", "reviews", ["source", "source_review_id"])

    op.create_table(
        "review_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("media_url", sa.String(length=500), nullable=False),
        sa.Column("alt_text", sa.String(length=200), nullable=True),
        sa.Column("consent_reference", sa.String(length=250), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_media_review_id", "review_media", ["review_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_review_media_review_id", table_name="review_media")
    op.drop_table("review_media")
    op.drop_constraint("uq_reviews_source_review_id", "reviews", type_="unique")
    for column_name in (
        "is_published",
        "reviewer_name",
        "source_listing_url",
        "source_product_id",
        "source_review_id",
        "source",
        "marketplace_verified_purchase",
    ):
        op.drop_column("reviews", column_name)
    op.alter_column("reviews", "user_id", existing_type=sa.Integer(), nullable=False)

    op.drop_column("product_variants", "measurements")
    for column_name in (
        "return_window_hours",
        "is_return_eligible",
        "is_exchange_eligible",
        "dispatch_days_max",
        "dispatch_days_min",
        "fit_note",
        "age_recommendation",
        "included_pieces",
        "lining",
        "is_most_loved",
    ):
        op.drop_column("products", column_name)

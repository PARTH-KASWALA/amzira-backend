"""add launch catalog metadata and seed kids categories

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-08-12 04:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("external_source", sa.String(50), nullable=True))
    op.add_column("products", sa.Column("external_id", sa.String(100), nullable=True))
    op.add_column("products", sa.Column("style_code", sa.String(100), nullable=True))
    op.add_column(
        "products",
        sa.Column("audience", sa.String(50), server_default="kids_girls", nullable=False),
    )
    op.add_column("products", sa.Column("collection", sa.String(100), nullable=True))
    op.add_column("products", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column(
        "products",
        sa.Column("catalog_status", sa.String(20), server_default="active", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("is_bestseller", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("is_new_arrival", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_products_external_source_id",
        "products",
        ["external_source", "external_id"],
    )
    op.create_index("ix_products_style_code", "products", ["style_code"], unique=False)

    op.execute(
        """
        INSERT INTO categories (name, slug, is_active, display_order)
        VALUES ('Kids', 'kids', TRUE, 1)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO categories (parent_id, name, slug, description, is_active, display_order)
        SELECT id, 'Girls Lehenga Choli', 'girls-lehenga-choli',
               'South Indian lehenga choli sets for girls', TRUE, 1
        FROM categories WHERE slug = 'kids'
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO categories (parent_id, name, slug, description, is_active, display_order)
        SELECT id, 'Pattu Pavadai', 'pattu-pavadai',
               'Traditional pattu pavadai sets for girls', TRUE, 2
        FROM categories WHERE slug = 'kids'
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO categories (parent_id, name, slug, description, is_active, display_order)
        SELECT id, 'South Indian Kids Ethnic Wear', 'south-indian-kids-ethnic-wear',
               'Festive South Indian ethnic wear for girls', TRUE, 3
        FROM categories WHERE slug = 'kids'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_products_style_code", table_name="products")
    op.drop_constraint("uq_products_external_source_id", "products", type_="unique")
    for column_name in (
        "is_new_arrival",
        "is_bestseller",
        "catalog_status",
        "tags",
        "collection",
        "audience",
        "style_code",
        "external_id",
        "external_source",
    ):
        op.drop_column("products", column_name)


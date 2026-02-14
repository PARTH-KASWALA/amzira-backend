"""Add categories parent_id and seed Kurti subcategory

Revision ID: f4d2b7e2c6b1
Revises: d4f2c8b9e7a1
Create Date: 2026-02-10 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4d2b7e2c6b1"
down_revision: Union[str, None] = "d4f2c8b9e7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"], unique=False)
    op.create_foreign_key(
        "fk_categories_parent_id",
        "categories",
        "categories",
        ["parent_id"],
        ["id"],
    )

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM categories WHERE slug = :slug"),
        {"slug": "kurti"},
    ).fetchone()
    if not existing:
        bind.execute(
            sa.text(
                """
                INSERT INTO categories (name, slug, description, is_active, display_order, parent_id)
                VALUES (:name, :slug, :description, :is_active, :display_order, :parent_id)
                """
            ),
            {
                "name": "Kurti",
                "slug": "kurti",
                "description": "Women kurti collection",
                "is_active": True,
                "display_order": 1,
                "parent_id": 6,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM categories WHERE slug = :slug AND parent_id = :parent_id"),
        {"slug": "kurti", "parent_id": 6},
    )

    op.drop_constraint("fk_categories_parent_id", "categories", type_="foreignkey")
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_column("categories", "parent_id")

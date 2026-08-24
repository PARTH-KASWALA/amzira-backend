"""Seed kids catalog design-family subcategories.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-08-14 15:35:00
"""

from alembic import op


revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


SUBCATEGORIES = (
    ("girls-lehenga-choli", "Artisan Work Lehenga", "artisan-work-lehenga"),
    ("girls-lehenga-choli", "Koti Jacket Sets", "koti-jacket-sets"),
    ("girls-lehenga-choli", "Black V Border", "black-v-border"),
    ("girls-lehenga-choli", "Satin Jacquard", "satin-jacquard"),
    ("pattu-pavadai", "Artisan Work Pattu Pavadai", "artisan-work-pattu"),
    ("pattu-pavadai", "Debli Jacquard", "debli-jacquard"),
    ("pattu-pavadai", "Peacock Jacquard", "peacock-jacquard"),
    ("pattu-pavadai", "Piramit Border", "piramit-border"),
    ("pattu-pavadai", "Elephant Jacquard", "elephant-jacquard"),
    ("pattu-pavadai", "Gold Jacquard", "gold-jacquard"),
    ("south-indian-kids-ethnic-wear", "Checked Butta", "checked-butta"),
)


def upgrade() -> None:
    connection = op.get_bind()
    for category_slug, name, slug in SUBCATEGORIES:
        connection.exec_driver_sql(
            """
            INSERT INTO subcategories (category_id, name, slug, is_active)
            SELECT id, %(name)s, %(slug)s, true
            FROM categories
            WHERE slug = %(category_slug)s
            ON CONFLICT (slug) DO UPDATE SET
                category_id = EXCLUDED.category_id,
                name = EXCLUDED.name,
                is_active = true
            """,
            {"category_slug": category_slug, "name": name, "slug": slug},
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "DELETE FROM subcategories WHERE slug = ANY(%(slugs)s)",
        {"slugs": [item[2] for item in SUBCATEGORIES]},
    )

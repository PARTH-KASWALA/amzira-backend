"""Consolidate inventory patterns into customer-facing South Indian sections.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-08-14 20:15:00
"""

from alembic import op


revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


SECTIONS = (
    ("girls-lehenga-choli", "South Indian Lehenga Choli", "south-indian-lehenga-choli"),
    ("girls-lehenga-choli", "Temple & Peacock Work Lehenga", "temple-peacock-work-lehenga"),
    ("girls-lehenga-choli", "Koti Jacket Lehenga Sets", "koti-jacket-lehenga-sets"),
    ("girls-lehenga-choli", "Festive Silk Lehenga Choli", "festive-silk-lehenga-choli"),
    ("pattu-pavadai", "Classic Pattu Pavadai", "classic-pattu-pavadai"),
    ("pattu-pavadai", "Peacock & Elephant Pattu Pavadai", "peacock-elephant-pattu-pavadai"),
    ("pattu-pavadai", "Gold Zari Pattu Pavadai", "gold-zari-pattu-pavadai"),
)

OLD_TO_NEW = (
    ("artisan-work-lehenga", "temple-peacock-work-lehenga"),
    ("koti-jacket-sets", "koti-jacket-lehenga-sets"),
    ("black-v-border", "south-indian-lehenga-choli"),
    ("satin-jacquard", "festive-silk-lehenga-choli"),
    ("artisan-work-pattu", "gold-zari-pattu-pavadai"),
    ("debli-jacquard", "classic-pattu-pavadai"),
    ("piramit-border", "classic-pattu-pavadai"),
    ("checked-butta", "classic-pattu-pavadai"),
    ("peacock-jacquard", "peacock-elephant-pattu-pavadai"),
    ("elephant-jacquard", "peacock-elephant-pattu-pavadai"),
    ("gold-jacquard", "gold-zari-pattu-pavadai"),
)

DOWNGRADE_FAMILIES = (
    ("455-Work/%", "artisan-work-lehenga", "temple-peacock-work-lehenga"),
    ("455-Work/%", "artisan-work-pattu", "gold-zari-pattu-pavadai"),
    ("456_Haresh_Checks/%", "checked-butta", "classic-pattu-pavadai"),
    ("443-Pratik_Debli/%", "debli-jacquard", "classic-pattu-pavadai"),
    ("445-Pratik_Piramit/%", "piramit-border", "classic-pattu-pavadai"),
    ("444-Pratik_mor_2/%", "peacock-jacquard", "peacock-elephant-pattu-pavadai"),
    ("449-Pratik_Hathi/%", "elephant-jacquard", "peacock-elephant-pattu-pavadai"),
    ("452-Pratik_Gold/%", "gold-jacquard", "gold-zari-pattu-pavadai"),
    ("446-Pratik_Koti_Pattern/%", "koti-jacket-sets", "koti-jacket-lehenga-sets"),
    ("448-Black V/%", "black-v-border", "south-indian-lehenga-choli"),
    ("453-Amzira_Satin/%", "satin-jacquard", "festive-silk-lehenga-choli"),
)


def _upsert_subcategory(connection, category_slug: str, name: str, slug: str) -> None:
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


def _move_products(connection, old_slug: str, new_slug: str) -> None:
    connection.exec_driver_sql(
        """
        UPDATE products AS product
        SET subcategory_id = destination.id,
            category_id = destination.category_id
        FROM subcategories AS source, subcategories AS destination
        WHERE product.subcategory_id = source.id
          AND source.slug = %(old_slug)s
          AND destination.slug = %(new_slug)s
        """,
        {"old_slug": old_slug, "new_slug": new_slug},
    )


def upgrade() -> None:
    connection = op.get_bind()
    for category_slug, name, slug in SECTIONS:
        _upsert_subcategory(connection, category_slug, name, slug)
    for old_slug, new_slug in OLD_TO_NEW:
        _move_products(connection, old_slug, new_slug)

    connection.exec_driver_sql(
        "UPDATE subcategories SET is_active = false WHERE slug = ANY(%(slugs)s)",
        {"slugs": [item[0] for item in OLD_TO_NEW]},
    )
    connection.exec_driver_sql(
        "UPDATE categories SET is_active = false WHERE slug = 'south-indian-kids-ethnic-wear'"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "UPDATE categories SET is_active = true WHERE slug = 'south-indian-kids-ethnic-wear'"
    )
    connection.exec_driver_sql(
        "UPDATE subcategories SET is_active = true WHERE slug = ANY(%(slugs)s)",
        {"slugs": [item[0] for item in OLD_TO_NEW]},
    )
    for external_id, old_slug, new_slug in DOWNGRADE_FAMILIES:
        connection.exec_driver_sql(
            """
            UPDATE products AS product
            SET subcategory_id = destination.id,
                category_id = destination.category_id
            FROM subcategories AS source, subcategories AS destination
            WHERE product.subcategory_id = source.id
              AND source.slug = %(new_slug)s
              AND destination.slug = %(old_slug)s
              AND product.external_id LIKE %(external_id)s
            """,
            {"new_slug": new_slug, "old_slug": old_slug, "external_id": external_id},
        )
    connection.exec_driver_sql(
        "DELETE FROM subcategories WHERE slug = ANY(%(slugs)s)",
        {"slugs": [item[2] for item in SECTIONS]},
    )

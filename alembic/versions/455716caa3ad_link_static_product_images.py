"""Link static product images to products

Revision ID: 455716caa3ad
Revises: a1f4b5c7d9e0
Create Date: 2026-02-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union
import os
import re
from pathlib import Path

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "455716caa3ad"
down_revision: Union[str, None] = "a1f4b5c7d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VIEW_PATTERN = re.compile(
    r"^(?P<base>.+?)(?:-(?P<view>front|side|back|detail|close|zoom|left|right|gallery)(?:-(?P<index>\\d+))?)?$"
)
_VIEW_ORDER = {
    "front": 0,
    "side": 1,
    "left": 1,
    "right": 1,
    "back": 2,
    "detail": 3,
    "close": 3,
    "zoom": 3,
    "gallery": 4,
}
_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _normalize_slug(stem: str) -> tuple[str, str | None, int | None]:
    value = stem.lower().replace("_", "-")
    match = _VIEW_PATTERN.match(value)
    if not match:
        return value, None, None
    base = match.group("base") or value
    view = match.group("view")
    index_raw = match.group("index")
    index = int(index_raw) if index_raw and index_raw.isdigit() else None
    return base, view, index


def upgrade() -> None:
    bind = op.get_bind()

    products = bind.execute(sa.text("SELECT id, slug, name FROM products")).fetchall()
    if not products:
        return

    product_by_slug = {row.slug: row for row in products}

    existing_rows = bind.execute(
        sa.text("SELECT product_id, image_url, display_order, is_primary FROM product_images")
    ).fetchall()

    existing_urls = {(row.product_id, row.image_url) for row in existing_rows}
    used_orders: dict[int, set[int]] = {}
    max_orders: dict[int, int] = {}
    has_primary: dict[int, bool] = {}

    for row in existing_rows:
        pid = int(row.product_id)
        order = int(row.display_order or 0)
        used_orders.setdefault(pid, set()).add(order)
        max_orders[pid] = max(max_orders.get(pid, 0), order)
        if row.is_primary:
            has_primary[pid] = True

    base_dir = Path(__file__).resolve().parents[2]
    static_dir = base_dir / "static" / "products"
    if not static_dir.exists():
        return

    product_images = sa.table(
        "product_images",
        sa.column("product_id", sa.Integer),
        sa.column("image_url", sa.String),
        sa.column("alt_text", sa.String),
        sa.column("display_order", sa.Integer),
        sa.column("is_primary", sa.Boolean),
    )

    inserts = []

    for file_path in sorted(static_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _ALLOWED_EXTS:
            continue

        stem = file_path.stem
        base_slug, view, index = _normalize_slug(stem)
        product = product_by_slug.get(base_slug)
        if not product:
            continue

        rel_path = file_path.relative_to(base_dir).as_posix()
        image_url = f"/{rel_path}"
        key = (product.id, image_url)
        if key in existing_urls:
            continue

        order = _VIEW_ORDER.get(view, 0)
        if index is not None:
            order += index

        used = used_orders.setdefault(product.id, set())
        if order in used:
            order = max_orders.get(product.id, 0) + 1

        used.add(order)
        max_orders[product.id] = max(max_orders.get(product.id, 0), order)

        alt_text = product.name
        if view:
            alt_text = view.replace("_", " ").title()

        is_primary = False
        if not has_primary.get(product.id, False) and view in (None, "front", "primary"):
            is_primary = True
            has_primary[product.id] = True

        inserts.append(
            {
                "product_id": product.id,
                "image_url": image_url,
                "alt_text": alt_text,
                "display_order": order,
                "is_primary": is_primary,
            }
        )

    if inserts:
        op.bulk_insert(product_images, inserts)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM product_images WHERE image_url LIKE :prefix"),
        {"prefix": "/static/products/%"},
    )

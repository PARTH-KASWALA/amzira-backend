"""Prepare a dry-run catalog upsert with search and confidence facts.

This deliberately does not create bestseller, most-loved, review, dispatch, or
return-policy claims. Those require marketplace or operational evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "build/catalog-full-inventory-local.json"
DEFAULT_OUTPUT = ROOT / "build/catalog-storefront-update.json"


def age_range(variants: list[dict]) -> str | None:
    ranges: list[tuple[int, int]] = []
    for variant in variants:
        match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)Y\s*", str(variant.get("size") or ""))
        if match:
            ranges.append((int(match.group(1)), int(match.group(2))))
    if not ranges:
        return None
    return f"{min(start for start, _ in ranges)}-{max(end for _, end in ranges)}Y"


def search_friendly_name(style_name: str, fabric: str, ages: str | None) -> str:
    suffix = f" for Girls ({ages})" if ages else " for Girls"
    return f"{style_name} in {fabric}{suffix} - Wedding & Festive"


def enrich_product(product: dict) -> dict:
    updated = dict(product)
    style_name = str(product.get("name") or "").strip()
    fabric = str(product.get("fabric") or "").strip()
    ages = age_range(list(product.get("variants") or []))
    if style_name and fabric and " for Girls (" not in style_name:
        updated["name"] = search_friendly_name(style_name, fabric, ages)

    product_name = str(updated.get("name") or style_name)
    updated["meta_title"] = f"{product_name} | AMZIRA"[:100]
    updated["meta_description"] = (
        f"Shop {product_name}, a ready-to-wear South Indian outfit with colour, fabric, size, and occasion details."
    )[:300]
    if ages:
        updated["age_recommendation"] = ages

    description = str(product.get("description") or "").lower()
    if "comfortable lining" in description:
        updated["lining"] = "Comfortable lining"
    if "one choli and one lehenga" in description:
        updated["included_pieces"] = ["Choli", "Lehenga"]

    # Existing catalog generation marked every SKU new. Preserve only a real
    # explicit input flag; the current source has none that meet that standard.
    updated["is_new_arrival"] = False
    updated.setdefault("is_most_loved", False)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    raw = json.loads(args.input.read_text(encoding="utf-8"))
    products = list(raw.get("products") or [])
    if not products:
        raise SystemExit("Catalog has no products")
    updated_products = [enrich_product(product) for product in products]
    output = {"products": updated_products, "mode": "upsert", "dry_run": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"products": len(updated_products), "output": str(args.output), "dry_run": True}))


if __name__ == "__main__":
    main()

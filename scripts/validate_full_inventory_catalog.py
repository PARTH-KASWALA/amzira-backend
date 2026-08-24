"""Validate the applied AMZIRA inventory catalog and its local media."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.product import Product


EXPECTED_CATEGORY_COUNTS = {
    "girls-lehenga-choli": 30,
    "pattu-pavadai": 77,
}
EXPECTED_SUBCATEGORY_COUNTS = {
    "south-indian-lehenga-choli": 13,
    "temple-peacock-work-lehenga": 7,
    "koti-jacket-lehenga-sets": 5,
    "festive-silk-lehenga-choli": 5,
    "classic-pattu-pavadai": 29,
    "peacock-elephant-pattu-pavadai": 22,
    "gold-zari-pattu-pavadai": 26,
}


def main() -> None:
    db = SessionLocal()
    errors: list[str] = []
    try:
        products = db.query(Product).filter(Product.external_source == "amzira_local_inventory").all()
        category_counts = Counter(product.category.slug for product in products)
        subcategory_counts = Counter(product.subcategory.slug if product.subcategory else "missing" for product in products)
        variants = sum(len(product.variants) for product in products)
        stock = sum(product.total_stock for product in products)
        images = sum(len(product.images) for product in products)

        if len(products) != 107:
            errors.append(f"Expected 107 products, found {len(products)}")
        if variants != 856:
            errors.append(f"Expected 856 variants, found {variants}")
        if stock != 1140:
            errors.append(f"Expected 1140 stock units, found {stock}")
        if dict(category_counts) != EXPECTED_CATEGORY_COUNTS:
            errors.append(f"Unexpected category counts: {dict(category_counts)}")
        if dict(subcategory_counts) != EXPECTED_SUBCATEGORY_COUNTS:
            errors.append(f"Unexpected subcategory counts: {dict(subcategory_counts)}")

        for product in products:
            if not product.description or len(product.description.strip()) < 80:
                errors.append(f"Missing description: {product.slug}")
            if product.sale_price is None or product.sale_price > product.base_price:
                errors.append(f"Invalid price: {product.slug}")
            if len(product.variants) != 8:
                errors.append(f"Expected 8 variants: {product.slug}")
            if not product.images:
                errors.append(f"Missing images: {product.slug}")
            for image in product.images:
                media_path = ROOT / urlparse(image.image_url).path.lstrip("/")
                if not media_path.is_file() or media_path.stat().st_size == 0:
                    errors.append(f"Missing local media: {image.image_url}")

        report = {
            "products": len(products),
            "variants": variants,
            "stock": stock,
            "images": images,
            "categories": dict(category_counts),
            "subcategories": dict(subcategory_counts),
            "errors": errors,
        }
        print(json.dumps(report, indent=2))
        if errors:
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

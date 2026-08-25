"""Build a Kids Girls catalog photo update from the renamed Amzira_Inventory shoot."""

from __future__ import annotations

import argparse
import csv
import json
import re
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_ROOT = Path("/Users/parthkaswala/Desktop/Amzira_Inventory")
BASE_CATALOG = ROOT / "build/catalog-full-inventory-local.json"
LOCAL_MEDIA_BASE = "http://localhost:8000/static/uploads/products/catalog"
DEFAULT_OUTPUT_JSON = ROOT / "build/catalog-updated-inventory-photos-local.json"
DEFAULT_MEDIA_MANIFEST = ROOT / "build/catalog-updated-inventory-photos-media.csv"

VIEW_ORDER = (
    "Front_View",
    "Closure_View",
    "Side_View",
    "Back_View",
    "Outfit",
    "Choli",
    "Lengha",
)
DISCOUNT_PERCENTAGE = Decimal("40")

PRODUCT_FOLDER_MAP = {
    "455-Work/1": "14/1",
    "455-Work/2": "14/2",
    "455-Work/4": "14/3",
    "455-Work/5": "14/22",
    "455-Work/6": "14/5",
    "455-Work/7": "14/6",
    "455-Work/8": "14/7",
    "455-Work/9": "14/8",
    "455-Work/10": "14/9",
    "455-Work/11": "14/10",
    "455-Work/12": "14/12",
    "455-Work/13": "14/13",
    "455-Work/14": "14/11",
    "455-Work/15": "14/14",
    "455-Work/16": "14/15",
    "455-Work/17": "14/16",
    "455-Work/18": "14/23",
    "455-Work/19": "14/18",
    "455-Work/20": "14/19",
    "455-Work/21": "14/20",
    "455-Work/22": "14/21",
    "456_Haresh_Checks/1": "15",
    "456_Haresh_Checks/2": "2/1",
    "456_Haresh_Checks/3": "2/2",
    "456_Haresh_Checks/4": "2/3",
    "456_Haresh_Checks/5": "2/4",
    "456_Haresh_Checks/6": "2/5",
    "443-Pratik_Debli/2": "3/2",
    "443-Pratik_Debli/3": "3/3",
    "443-Pratik_Debli/4": "3/1",
    "443-Pratik_Debli/5": "3/4",
    "443-Pratik_Debli/6": "3/5",
    "443-Pratik_Debli/7": "3/6",
    "443-Pratik_Debli/8": "3/7",
    "443-Pratik_Debli/9": "3/8",
    "443-Pratik_Debli/10": "13",
    "443-Pratik_Debli/11": "8",
    "443-Pratik_Debli/12": "3/9/2MB",
    "444-Pratik_mor_2/1": "4/1",
    "444-Pratik_mor_2/2": "4/2",
    "444-Pratik_mor_2/3": "4/12",
    "444-Pratik_mor_2/4": "4/4",
    "444-Pratik_mor_2/5": "4/5",
    "444-Pratik_mor_2/6": "4/6",
    "444-Pratik_mor_2/7": "63",
    "444-Pratik_mor_2/8": "4/8",
    "444-Pratik_mor_2/9": "4/9/2MB",
    "444-Pratik_mor_2/10": "4/10",
    "444-Pratik_mor_2/11": "91",
    "444-Pratik_mor_2/12": "76",
    "445-Pratik_Piramit/1": "6/1",
    "445-Pratik_Piramit/2": "6/2",
    "445-Pratik_Piramit/3": "5/1",
    "445-Pratik_Piramit/4": "6/3",
    "445-Pratik_Piramit/5": "5/2",
    "445-Pratik_Piramit/8": "5/4",
    "445-Pratik_Piramit/9": "5/5",
    "445-Pratik_Piramit/10": "5/6",
    "445-Pratik_Piramit/11": "5/7",
    "445-Pratik_Piramit/12": "6/4",
    "445-Pratik_Piramit/13": "5/8",
    "445-Pratik_Piramit/14": "5/9",
    "446-Pratik_Koti_Pattern/1": "7/1",
    "446-Pratik_Koti_Pattern/2": "7/2",
    "446-Pratik_Koti_Pattern/3": "7/3",
    "446-Pratik_Koti_Pattern/4": "7/4",
    "446-Pratik_Koti_Pattern/5": "7/5",
    "448-Black V/1": "9/1",
    "448-Black V/2": "1/2",
    "448-Black V/3": "9/2",
    "448-Black V/4": "9/3",
    "448-Black V/5": "9/4",
    "448-Black V/6": "9/5",
    "448-Black V/7": "1/3",
    "448-Black V/8": "9/6",
    "448-Black V/10": "9/7",
    "448-Black V/11": "1/6",
    "448-Black V/12": "9/8",
    "448-Black V/13": "9/9",
    "448-Black V/14": "9/10",
    "449-Pratik_Hathi/1": "10/1",
    "449-Pratik_Hathi/2": "10/2",
    "449-Pratik_Hathi/3": "10/3",
    "449-Pratik_Hathi/4": "10/4",
    "449-Pratik_Hathi/5": "10/5",
    "449-Pratik_Hathi/6": "10/6",
    "449-Pratik_Hathi/7": "10/9",
    "449-Pratik_Hathi/8": "10/8",
    "449-Pratik_Hathi/9": "10/9",
    "449-Pratik_Hathi/10": "10/10",
    "452-Pratik_Gold/1": "11/1",
    "452-Pratik_Gold/2": "11/6",
    "452-Pratik_Gold/3": "11/2",
    "452-Pratik_Gold/4": "11/4",
    "452-Pratik_Gold/5": "11/5",
    "452-Pratik_Gold/6": "11/6",
    "452-Pratik_Gold/7": "11/7",
    "452-Pratik_Gold/8": "11/8",
    "452-Pratik_Gold/9": "11/9",
    "452-Pratik_Gold/10": "11/10",
    "452-Pratik_Gold/11": "11/3",
    "452-Pratik_Gold/12": "11/1",
    "453-Amzira_Satin/1": "12/1",
    "453-Amzira_Satin/2": "12/2",
    "453-Amzira_Satin/3": "12/3",
    "453-Amzira_Satin/4": "12/4",
    "453-Amzira_Satin/5": "12/5",
}

NEW_PRODUCT_FOLDERS = ("1/1", "1/5", "10/8", "101")


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def stock_left(slug: str, size: str) -> int:
    seed = f"{slug}:{size}"
    checksum = sum((index + 1) * ord(character) for index, character in enumerate(seed))
    return 21 + (checksum % 30)


def apply_discount(product: dict) -> None:
    base_price = Decimal(str(product["base_price"]))
    sale_price = (base_price * (Decimal("100") - DISCOUNT_PERCENTAGE) / Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    product["sale_price"] = format(sale_price, "f")


def view_images(folder: Path) -> list[Path]:
    files = [
        item
        for item in folder.iterdir()
        if item.is_file()
        and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        and not item.name.lower().startswith("thumbs")
    ]
    by_stem = {item.stem.lower(): item for item in files}
    ordered = [by_stem[name.lower()] for name in VIEW_ORDER if name.lower() in by_stem]
    if ordered:
        return ordered

    def fallback_key(item: Path) -> tuple[int, int, str]:
        stem = item.stem
        if stem == "1":
            return (0, 0, item.name)
        if stem.upper().startswith("KLC-"):
            return (1, 0, item.name)
        if stem.isdigit():
            return (2, int(stem), item.name)
        return (3, 0, item.name)

    return sorted(files, key=fallback_key)


def image_payloads(product: dict, folder_rel: str, media_base_url: str, media_prefix: str) -> tuple[list[dict], list[dict]]:
    folder = INVENTORY_ROOT / folder_rel
    if not folder.is_dir():
        # A previously prepared local catalog is a safe fallback when the original
        # shoot folder has been moved after the catalog was imported.
        folder = ROOT / "static/uploads/products/catalog" / product["slug"]
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    sources = view_images(folder)
    if not sources:
        raise ValueError(f"No images found for {folder_rel}")

    payloads = []
    rows = []
    for order, source in enumerate(sources, start=1):
        url = f"{media_base_url.rstrip('/')}/{product['slug']}/{order:02d}.webp"
        view_label = "Front View" if order == 1 else source.stem.replace("_", " ").title()
        payloads.append(
            {
                "image_url": url,
                "alt_text": f"{product['name']} - {view_label}",
                "display_order": order - 1,
                "is_primary": order == 1,
            }
        )
        rows.append(
            {
                "product_slug": product["slug"],
                "display_order": order - 1,
                "is_primary": str(order == 1).lower(),
                "source_path": str(source),
                "r2_key": f"{media_prefix.strip('/')}/{product['slug']}/{order:02d}.webp",
                "public_url": url,
            }
        )
    return payloads, rows


def new_product_from_template(template: dict, folder_rel: str, index: int) -> dict:
    product = deepcopy(template)
    name = f"AMZIRA Girls Festive Lehenga Choli {folder_rel.replace('/', '-')}"
    slug = slugify(name)
    product.update(
        {
            "name": name,
            "slug": slug,
            "external_id": f"updated-photoshoot/{folder_rel}",
            "style_code": f"AMZ-UPD-{folder_rel.replace('/', '-')}",
            "is_featured": False,
            "is_bestseller": False,
            "is_new_arrival": True,
        }
    )
    for variant in product["variants"]:
        variant["sku"] = f"{product['style_code']}-{variant['sku'].split('-')[-1]}"
        variant["stock_quantity"] = stock_left(slug, variant["size"])
    return product


def build(
    *,
    media_base_url: str = LOCAL_MEDIA_BASE,
    media_prefix: str = "catalog",
    output_json: Path = DEFAULT_OUTPUT_JSON,
    output_manifest: Path = DEFAULT_MEDIA_MANIFEST,
) -> dict:
    base = json.loads(BASE_CATALOG.read_text(encoding="utf-8"))
    products = []
    media_rows = []
    used_folders: set[str] = set()

    for product in base["products"]:
        item = deepcopy(product)
        folder_rel = PRODUCT_FOLDER_MAP[item["external_id"]]
        apply_discount(item)
        item["images"], rows = image_payloads(item, folder_rel, media_base_url, media_prefix)
        for variant in item["variants"]:
            variant["stock_quantity"] = stock_left(item["slug"], variant["size"])
        products.append(item)
        media_rows.extend(rows)
        used_folders.add(folder_rel)

    template = next(item for item in products if item["category_slug"] == "girls-lehenga-choli")
    for index, folder_rel in enumerate(NEW_PRODUCT_FOLDERS, start=1):
        if folder_rel in used_folders:
            continue
        product = new_product_from_template(template, folder_rel, index)
        product["images"], rows = image_payloads(product, folder_rel, media_base_url, media_prefix)
        products.append(product)
        media_rows.extend(rows)
        used_folders.add(folder_rel)

    output_json.write_text(
        json.dumps({"products": products, "mode": "upsert", "dry_run": True}, indent=2),
        encoding="utf-8",
    )
    with output_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["product_slug", "display_order", "is_primary", "source_path", "r2_key", "public_url"],
        )
        writer.writeheader()
        writer.writerows(media_rows)

    report = {
        "existing_products_updated": len(base["products"]),
        "new_products_added": len(products) - len(base["products"]),
        "products": len(products),
        "media": len(media_rows),
        "json": str(output_json),
        "manifest": str(output_manifest),
        "media_base_url": media_base_url,
        "media_prefix": media_prefix,
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the updated inventory photo catalog")
    parser.add_argument("--media-base-url", default=LOCAL_MEDIA_BASE)
    parser.add_argument("--media-prefix", default="catalog")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_MEDIA_MANIFEST)
    args = parser.parse_args()
    build(
        media_base_url=args.media_base_url,
        media_prefix=args.media_prefix,
        output_json=args.output_json,
        output_manifest=args.output_manifest,
    )

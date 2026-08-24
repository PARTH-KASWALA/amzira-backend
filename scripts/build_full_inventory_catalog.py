"""Build an idempotent local catalog from every listable AMZIRA inventory folder."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from build_launch_catalog import PRODUCTS as LAUNCH_PRODUCTS
from build_launch_catalog import SIZES, slugify, stock_quantities


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_ROOT = Path("/Users/parthkaswala/Desktop/Amzira_Inventory")
FRONTEND_ROOT = Path("/Users/parthkaswala/Desktop/amzira-frontend")
OUTPUT_JSON = ROOT / "build/catalog-full-inventory-local.json"
OUTPUT_CSV = ROOT / "build/catalog-full-inventory-local.csv"
MEDIA_MANIFEST = ROOT / "build/catalog-full-inventory-media.csv"
LOCAL_MEDIA_BASE = "http://localhost:8000/static/uploads/products/catalog"

CSV_HEADERS = [
    "product_slug", "name", "category_slug", "subcategory_slug", "base_price", "sale_price",
    "description", "fabric", "care_instructions", "meta_title", "meta_description", "audience",
    "collection", "tags", "status", "is_featured", "is_bestseller", "is_new_arrival",
    "occasion_slugs", "image_urls", "sku", "size", "color", "stock_quantity", "additional_price",
    "variant_is_active", "external_source", "external_id", "style_code",
]


@dataclass(frozen=True)
class Family:
    category: str
    subcategory: str
    collection: str
    product_type: str
    motif: str
    fabric: str
    base_price: int
    sale_price: int


FAMILIES = {
    "443-Pratik_Debli": Family(
        "pattu-pavadai", "classic-pattu-pavadai", "AMZIRA Classic Pattu",
        "Pattu Pavadai", "geometric jacquard weaving and a traditional zari border",
        "Art Silk Jacquard", 1899, 1299,
    ),
    "444-Pratik_mor_2": Family(
        "pattu-pavadai", "peacock-elephant-pattu-pavadai", "AMZIRA Heritage Motif Pattu",
        "Pattu Pavadai", "woven peacock jacquard motifs and a festive zari border",
        "Art Silk Jacquard", 1999, 1399,
    ),
    "445-Pratik_Piramit": Family(
        "pattu-pavadai", "classic-pattu-pavadai", "AMZIRA Classic Pattu",
        "Pattu Pavadai", "a structured heritage border and classic festive detailing",
        "Art Silk", 1899, 1299,
    ),
    "446-Pratik_Koti_Pattern": Family(
        "girls-lehenga-choli", "koti-jacket-lehenga-sets", "AMZIRA Koti Jacket Sets",
        "Koti Jacket Lehenga Choli", "a coordinated jacquard koti jacket and woven festive border",
        "Art Silk Jacquard", 2299, 1599,
    ),
    "448-Black V": Family(
        "girls-lehenga-choli", "south-indian-lehenga-choli", "AMZIRA South Indian Lehenga",
        "Lehenga Choli", "a distinctive contrast V border and traditional woven accents",
        "Art Silk", 2099, 1449,
    ),
    "449-Pratik_Hathi": Family(
        "pattu-pavadai", "peacock-elephant-pattu-pavadai", "AMZIRA Heritage Motif Pattu",
        "Pattu Pavadai", "heritage elephant jacquard motifs and a lustrous gold border",
        "Art Silk Jacquard", 2199, 1499,
    ),
    "452-Pratik_Gold": Family(
        "pattu-pavadai", "gold-zari-pattu-pavadai", "AMZIRA Gold Zari Pattu",
        "Pattu Pavadai", "rich gold jacquard work and a celebration-ready woven border",
        "Art Silk Jacquard", 2099, 1449,
    ),
    "453-Amzira_Satin": Family(
        "girls-lehenga-choli", "festive-silk-lehenga-choli", "AMZIRA Festive Silk Choli",
        "Lehenga Choli", "a soft satin finish, gold jacquard details and a graceful flared skirt",
        "Satin Jacquard", 2299, 1599,
    ),
}

COLOR_NAMES = {
    "AQU": "Aqua", "BLK": "Black", "BLU": "Blue", "BPK": "Baby Pink",
    "CRM": "Cream", "DGN": "Dark Green", "EMG": "Emerald Green", "GL": "Gold",
    "GLD": "Gold", "GRN": "Green", "HPK": "Hot Pink", "IVR": "Ivory",
    "LGR": "Light Green", "LGN": "Light Green", "LME": "Lime", "MINT": "Mint",
    "MNT": "Mint", "MRN": "Maroon", "MST": "Mustard", "MUS": "Mustard",
    "NBL": "Navy Blue", "NVY": "Navy", "OLV": "Olive", "ORG": "Orange",
    "PCB": "Peacock Blue", "PCH": "Peach", "PNK": "Pink", "PPL": "Purple",
    "PST": "Pistachio", "PUR": "Purple", "RBL": "Royal Blue", "RED": "Red",
    "RPK": "Rani Pink", "SBL": "Sky Blue", "SKB": "Sky Blue", "SKYBLU": "Sky Blue",
    "TBL": "Teal Blue", "TEA": "Teal", "TEAL": "Teal", "TEL": "Teal",
    "WINE": "Wine", "YLW": "Yellow",
}

GIRL_NAMES = (
    "Aanya", "Aarini", "Aashi", "Aditi", "Ahana", "Akshara", "Alina", "Amaya", "Anaya", "Anvi",
    "Avika", "Charvi", "Devika", "Dhriti", "Eesha", "Elina", "Iha", "Inaaya", "Ishita", "Jivika",
    "Kaira", "Kashvi", "Keya", "Krisha", "Larisa", "Mahika", "Mira", "Mishka", "Naisha", "Navika",
    "Neysa", "Nitya", "Ojasvi", "Pari", "Pranavi", "Raina", "Reeva", "Riddhi", "Ruhi", "Saesha",
    "Samaira", "Sanaya", "Shanaya", "Sharvi", "Shloka", "Suhana", "Tanvi", "Trisha", "Urvi", "Veda",
    "Vedika", "Viha", "Yashvi", "Zara", "Aarna", "Aarika", "Advika", "Anika", "Anushka", "Avni",
    "Diya", "Gauri", "Ira", "Kiara", "Mahi", "Meera", "Myra", "Navya", "Nila", "Prisha",
    "Riya", "Saanvi", "Siya", "Tara", "Vanya", "Aadhya", "Aaradhya", "Amaira", "Ishani", "Neela",
)

LAUNCH_BY_FOLDER = {item[0]: item for item in LAUNCH_PRODUCTS}
LAUNCH_SUBCATEGORIES = {
    ("455-Work", "girls-lehenga-choli"): "temple-peacock-work-lehenga",
    ("455-Work", "pattu-pavadai"): "gold-zari-pattu-pavadai",
    ("456_Haresh_Checks", "pattu-pavadai"): "classic-pattu-pavadai",
}


def natural_key(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value))


def inventory_images(folder: Path) -> list[Path]:
    candidates = [
        item for item in folder.rglob("*")
        if item.is_file()
        and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        and not re.search(r"chatgpt|not[-_ ]?now|thumbs", item.name, re.IGNORECASE)
    ]
    selected: dict[str, Path] = {}
    for item in sorted(candidates, key=lambda path: (len(path.relative_to(folder).parts), path.name.lower())):
        stem = re.sub(r"\(\d+\)$", "", item.stem).lower()
        key = "product-code" if stem.startswith("klc-") else stem
        selected.setdefault(key, item)

    def sort_key(item: Path) -> tuple[int, int, str]:
        stem = item.stem
        if stem == "1":
            return (0, 1, item.name)
        if stem.upper().startswith("KLC-"):
            return (1, 0, item.name)
        if stem.isdigit():
            return (2, int(stem), item.name)
        return (3, 0, item.name)

    return sorted(selected.values(), key=sort_key)[:8]


def source_style_code(folder: Path, relative_folder: str) -> str:
    code_images = [item for item in inventory_images(folder) if item.stem.upper().startswith("KLC-")]
    if code_images:
        raw = re.sub(r"\(\d+\)$", "", code_images[0].stem.upper())
        normalized = re.sub(r"-+", "-", re.sub(r"[^A-Z0-9]+", "-", raw)).strip("-")
        if normalized:
            return normalized
    family, product_folder = relative_folder.split("/", 1)
    family_number = re.match(r"\d+", family)
    return f"AMZ-{family_number.group(0) if family_number else 'INV'}-{slugify(product_folder).upper()}"


def color_from_style_code(style_code: str) -> str:
    tokens = style_code.upper().split("-")[1:]
    colors: list[str] = []
    stop_tokens = {"JAQ", "JQD", "JQ", "JQQ", "KOTI", "TRD", "WRK", "WORK", "PEACOCKJAQ"}
    for token in tokens:
        if token in stop_tokens or token == "00" or token.isdigit() or any(word in token for word in ("WORK", "JAQ")):
            break
        color = COLOR_NAMES.get(token)
        if color and color not in colors:
            colors.append(color)
        if len(colors) == 3:
            break
    return " / ".join(colors) if colors else "Heritage Multi"


def generated_products() -> list[tuple[str, Family]]:
    products: list[tuple[str, Family]] = []
    for family_name, config in FAMILIES.items():
        family_folder = INVENTORY_ROOT / family_name
        for product_folder in sorted((item for item in family_folder.iterdir() if item.is_dir()), key=lambda item: natural_key(item.name)):
            if "pending" in product_folder.name.lower() or product_folder.name == "3_Year":
                continue
            if inventory_images(product_folder):
                products.append((f"{family_name}/{product_folder.name}", config))
    return products


def product_payload(
    *,
    relative_folder: str,
    name: str,
    category: str,
    subcategory: str,
    color: str,
    motif: str,
    fabric: str,
    collection: str,
    base_price: int,
    sale_price: int,
    style_code: str,
    stock: list[int],
    featured: bool,
) -> tuple[dict, list[dict], list[dict]]:
    folder = INVENTORY_ROOT / relative_folder
    slug = slugify(name)
    image_payloads = []
    media_rows = []
    image_urls = []
    for order, source in enumerate(inventory_images(folder), start=1):
        relative_key = f"catalog/{slug}/{order:02d}.webp"
        image_url = f"{LOCAL_MEDIA_BASE}/{slug}/{order:02d}.webp"
        image_urls.append(image_url)
        image_payloads.append({
            "image_url": image_url,
            "alt_text": f"{name} - view {order}",
            "display_order": order - 1,
            "is_primary": order == 1,
        })
        media_rows.append({
            "product_slug": slug,
            "display_order": order - 1,
            "is_primary": str(order == 1).lower(),
            "source_path": str(source),
            "r2_key": relative_key,
            "public_url": image_url,
        })

    description = (
        f"A ready-to-wear South Indian {name.split(' ', 1)[1]} for girls in {color.lower()}, "
        f"finished with {motif}. Designed with a comfortable lining and celebration-friendly flare "
        "for weddings, festivals, birthdays, temple ceremonies and family gatherings. "
        "The coordinated set includes one choli and one lehenga."
    )
    common = {
        "product_slug": slug,
        "name": name,
        "category_slug": category,
        "subcategory_slug": subcategory,
        "base_price": f"{base_price:.2f}",
        "sale_price": f"{sale_price:.2f}",
        "description": description,
        "fabric": fabric,
        "care_instructions": "Dry clean recommended. Store folded in a cool, dry place away from direct sunlight.",
        "meta_title": f"{name} for Girls | AMZIRA"[:100],
        "meta_description": f"Shop {name}, a ready-to-wear South Indian festive set for girls aged 1-10 years."[:300],
        "audience": "kids_girls",
        "collection": collection,
        "tags": "|".join((category, subcategory, "south-indian", "festive", "ready-to-wear")),
        "status": "active",
        "is_featured": str(featured).lower(),
        "is_bestseller": "false",
        "is_new_arrival": "true",
        "occasion_slugs": "festival|wedding|temple-ceremony|birthday",
        "image_urls": "|".join(image_urls),
        "external_source": "amzira_local_inventory",
        "external_id": relative_folder,
        "style_code": style_code,
    }
    variants = []
    csv_rows = []
    for (size_code, age_band), quantity in zip(SIZES, stock, strict=True):
        sku = f"{style_code}-{size_code}"
        variants.append({
            "sku": sku,
            "size": age_band,
            "color": color,
            "stock_quantity": quantity,
            "additional_price": "0.00",
            "is_active": True,
        })
        csv_rows.append({
            **common,
            "sku": sku,
            "size": age_band,
            "color": color,
            "stock_quantity": quantity,
            "additional_price": "0.00",
            "variant_is_active": "true",
        })

    product = {
        "name": name,
        "slug": slug,
        "category_slug": category,
        "subcategory_slug": subcategory,
        "base_price": str(base_price),
        "sale_price": str(sale_price),
        "description": description,
        "fabric": fabric,
        "care_instructions": common["care_instructions"],
        "meta_title": common["meta_title"],
        "meta_description": common["meta_description"],
        "audience": "kids_girls",
        "collection": collection,
        "tags": common["tags"].split("|"),
        "status": "active",
        "is_featured": featured,
        "is_bestseller": False,
        "is_new_arrival": True,
        "occasion_slugs": common["occasion_slugs"].split("|"),
        "images": image_payloads,
        "variants": variants,
        "external_source": common["external_source"],
        "external_id": relative_folder,
        "style_code": style_code,
    }
    return product, csv_rows, media_rows


def build() -> dict:
    products = []
    csv_rows = []
    media_rows = []
    seen_slugs: set[str] = set()
    seen_skus: set[str] = set()

    for index, item in enumerate(LAUNCH_PRODUCTS):
        relative_folder, base_sku, name, color, motif, category, base_price, sale_price = item
        family_name = relative_folder.split("/", 1)[0]
        customer_category = "pattu-pavadai" if family_name == "456_Haresh_Checks" else category
        subcategory = LAUNCH_SUBCATEGORIES[(family_name, customer_category)]
        fabric = "Art Silk Jacquard" if family_name == "455-Work" else "Silk Blend"
        product, rows, media = product_payload(
            relative_folder=relative_folder,
            name=name,
            category=customer_category,
            subcategory=subcategory,
            color=color,
            motif=motif.lower() + " detailing and a traditional woven border",
            fabric=fabric,
            collection=(
                "AMZIRA Temple & Peacock Work" if family_name == "455-Work" and customer_category == "girls-lehenga-choli"
                else "AMZIRA Gold Zari Pattu" if family_name == "455-Work"
                else "AMZIRA Classic Pattu"
            ),
            base_price=base_price,
            sale_price=sale_price,
            style_code=base_sku,
            stock=stock_quantities(index),
            featured=index < 6,
        )
        products.append(product)
        csv_rows.extend(rows)
        media_rows.extend(media)

    for index, (relative_folder, config) in enumerate(generated_products()):
        folder = INVENTORY_ROOT / relative_folder
        style_code = source_style_code(folder, relative_folder)
        color = color_from_style_code(style_code)
        display_color = color.replace(" / ", " ")
        girl_name = GIRL_NAMES[index % len(GIRL_NAMES)]
        name = f"{girl_name} {display_color} {config.product_type}"
        product, rows, media = product_payload(
            relative_folder=relative_folder,
            name=name,
            category=config.category,
            subcategory=config.subcategory,
            color=color,
            motif=config.motif,
            fabric=config.fabric,
            collection=config.collection,
            base_price=config.base_price,
            sale_price=config.sale_price,
            style_code=style_code,
            stock=[1] * len(SIZES),
            featured=index < len(FAMILIES),
        )
        products.append(product)
        csv_rows.extend(rows)
        media_rows.extend(media)

    for product in products:
        if product["slug"] in seen_slugs:
            raise ValueError(f"Duplicate product slug: {product['slug']}")
        seen_slugs.add(product["slug"])
        for variant in product["variants"]:
            if variant["sku"] in seen_skus:
                raise ValueError(f"Duplicate SKU: {variant['sku']}")
            seen_skus.add(variant["sku"])

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({"products": products, "mode": "upsert", "dry_run": True}, indent=2), encoding="utf-8")
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(csv_rows)
    with MEDIA_MANIFEST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["product_slug", "display_order", "is_primary", "source_path", "r2_key", "public_url"],
        )
        writer.writeheader()
        writer.writerows(media_rows)

    total_stock = sum(variant["stock_quantity"] for product in products for variant in product["variants"])
    report = {
        "products": len(products),
        "variants": len(seen_skus),
        "stock": total_stock,
        "media": len(media_rows),
        "json": str(OUTPUT_JSON),
        "csv": str(OUTPUT_CSV),
        "manifest": str(MEDIA_MANIFEST),
        "frontend": str(FRONTEND_ROOT),
    }
    if report["products"] != 107 or report["variants"] != 856 or report["stock"] != 1140:
        raise AssertionError(report)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    build()

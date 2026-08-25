"""Build the first AMZIRA Work + Haresh Butta production catalog batch."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_ROOT = Path("/Users/parthkaswala/Desktop/Amzira_Inventory")
OUTPUT_CSV = ROOT / "docs/catalog-launch-work-haresh.csv"
OUTPUT_JSON = ROOT / "docs/catalog-launch-work-haresh.json"
MEDIA_MANIFEST = ROOT / "docs/catalog-media-manifest.csv"
LOCAL_OUTPUT_CSV = ROOT / "build/catalog-launch-work-haresh-local.csv"
LOCAL_OUTPUT_JSON = ROOT / "build/catalog-launch-work-haresh-local.json"
CDN_BASE = "https://cdn.amzira.com/catalog"
LOCAL_MEDIA_BASE = "http://localhost:8000/static/uploads/products/catalog"

# The launch catalog keeps the original inventory IDs for SKU traceability,
# while the local photo shoot was reorganized into numbered folders. Keep the
# mapping here so a regenerated manifest points at the current source files.
INVENTORY_FOLDER_MAP = {
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
}

SIZES = [
    ("18", "1-2Y"),
    ("20", "2-3Y"),
    ("21", "3-4Y"),
    ("22", "4-5Y"),
    ("24", "5-6Y"),
    ("26", "6-7Y"),
    ("28", "7-8Y"),
    ("33", "9-10Y"),
]

PRODUCTS = [
    ("455-Work/1", "KLC-GRN-PUR-WRK-00-0330", "Anika Emerald Purple Temple Work Lehenga Choli", "Emerald Green / Purple", "Temple Work", "girls-lehenga-choli", 1999, 1399),
    ("455-Work/2", "KLC-BLU-PCH-WRK-00-0331", "Meera Royal Blue Peach Peacock Lehenga Choli", "Royal Blue / Peach", "Peacock Work", "girls-lehenga-choli", 1999, 1399),
    ("455-Work/4", "KLC-BLU-MUS-WRK-00-0343", "Aadhya Royal Blue Mustard Temple Border Lehenga Choli", "Royal Blue / Mustard", "Temple Border", "girls-lehenga-choli", 1999, 1399),
    ("455-Work/5", "KLC-WINE-GRN-PEACOCKWORK-00-0454", "Vanya Wine Green Peacock Work Pattu Pavadai", "Wine / Green", "Peacock Work", "girls-lehenga-choli", 2199, 1499),
    ("455-Work/6", "KLC-GRN-MRN-PEACOCKWRK-00-0423", "Ira Emerald Maroon Peacock Work Lehenga Choli", "Emerald Green / Maroon", "Peacock Work", "girls-lehenga-choli", 2199, 1499),
    ("455-Work/7", "KLC-LGN-RED-RBLU-JAQ-WRK-00-0439", "Nila Red Royal Blue Jacquard Work Lehenga Choli", "Red / Royal Blue", "Jacquard Work", "girls-lehenga-choli", 2099, 1449),
    ("455-Work/8", "KLC-PCH-PUR-PEACOCKWORK-00-0443", "Tara Peach Purple Peacock Work Pattu Pavadai", "Peach / Purple", "Peacock Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/9", "KLC-NVY-RED-PEACOCKWORK-00-0444", "Kavya Navy Red Peacock Work Lehenga Choli", "Navy / Red", "Peacock Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/10", "KLC-YLW-PNK-PEACOCKPOTWORK-00-0449", "Diya Yellow Pink Peacock Butta Pattu Pavadai", "Yellow / Pink", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/11", "KLC-PUR-OLV-TREEDEERWORK-00-0442", "Riya Purple Olive Tree Deer Pattu Pavadai", "Purple / Olive", "Tree Deer Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/12", "KLC-TEAL-MUS-PEACOCKPOTWORK-00-0446", "Aarna Teal Mustard Peacock Butta Pattu Pavadai", "Teal / Mustard", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/13", "KLC-PUR-PCH-PEACOCKWORK-00-0445", "Avni Purple Peach Peacock Work Pattu Pavadai", "Purple / Peach", "Peacock Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/14", "KLC-MRN-MNT-TREEDEERWORK-00-0447", "Siya Maroon Mint Tree Deer Pattu Pavadai", "Maroon / Mint", "Tree Deer Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/15", "KLC-TEAL-PST-PEACOCKPOTWORK-00-0448", "Myra Teal Pistachio Peacock Butta Pattu Pavadai", "Teal / Pistachio", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/16", "KLC-PUR-GRN-PEACOCKPOTWORK-00-0451", "Prisha Purple Green Peacock Butta Pattu Pavadai", "Purple / Green", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/17", "KLC-CRM-PNK-TREEDEERWORK-00-0452", "Aaradhya Cream Pink Tree Deer Pattu Pavadai", "Cream / Pink", "Tree Deer Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/18", "KLC-LME-YLW-PEACOCKPOTWORK-00-0450", "Navya Lime Yellow Peacock Butta Pattu Pavadai", "Lime / Yellow", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/19", "KLC-GRN-BLU-PEACOCKPOTWORK-00-0453", "Anvi Green Blue Peacock Butta Pattu Pavadai", "Green / Blue", "Peacock Butta", "pattu-pavadai", 2199, 1499),
    ("455-Work/20", "KLC-GRN-MRN-PEACOCKWORK-00-0440", "Kiara Green Maroon Peacock Work Pattu Pavadai", "Green / Maroon", "Peacock Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/21", "KLC-YLW-GRN-TREEDEERWORK-00-0441", "Saanvi Yellow Green Tree Deer Pattu Pavadai", "Yellow / Green", "Tree Deer Work", "pattu-pavadai", 2199, 1499),
    ("455-Work/22", "KLC-LGN-RED-NBLU-JAQ-WRK-00-0440", "Amaira Light Green Red Jacquard Work Lehenga Choli", "Light Green / Red / Navy", "Jacquard Work", "girls-lehenga-choli", 2099, 1449),
    ("456_Haresh_Checks/1", "KLC-HBT-RBL-GLD-CHK-0456-01", "Neela Royal Blue Checked Butta Pattu Pavadai", "Royal Blue / Gold", "Checked Butta", "pattu-pavadai", 1899, 1299),
    ("456_Haresh_Checks/2", "KLC-HBT-GRN-RPK-CHK-0456-02", "Gauri Green Rani Pink Checked Butta Pattu Pavadai", "Green / Rani Pink", "Checked Butta", "pattu-pavadai", 1899, 1299),
    ("456_Haresh_Checks/3", "KLC-HBT-PUR-GRN-CHK-0456-03", "Mahi Purple Green Checked Butta Pattu Pavadai", "Purple / Green", "Checked Butta", "pattu-pavadai", 1899, 1299),
    ("456_Haresh_Checks/4", "KLC-HBT-RBL-IVR-CHK-0456-04", "Tara Royal Blue Ivory Checked Butta Pattu Pavadai", "Royal Blue / Ivory", "Checked Butta", "pattu-pavadai", 1899, 1299),
    ("456_Haresh_Checks/5", "KLC-HBT-RBL-ORG-CHK-0456-05", "Aarohi Royal Blue Orange Checked Butta Pattu Pavadai", "Royal Blue / Orange", "Checked Butta", "pattu-pavadai", 1899, 1299),
    ("456_Haresh_Checks/6", "KLC-HBT-RED-BLK-CHK-0456-06", "Ishani Red Black Checked Butta Pattu Pavadai", "Red / Black", "Checked Butta", "pattu-pavadai", 1899, 1299),
]

CSV_HEADERS = [
    "product_slug", "name", "category_slug", "subcategory_slug", "base_price", "sale_price",
    "description", "fabric", "care_instructions", "meta_title", "meta_description", "audience",
    "collection", "tags", "status", "is_featured", "is_bestseller", "is_new_arrival",
    "occasion_slugs", "image_urls", "sku", "size", "color", "stock_quantity", "additional_price",
    "variant_is_active", "external_source", "external_id", "style_code",
]


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def product_images(folder: Path) -> list[Path]:
    images = [
        item for item in folder.iterdir()
        if item.is_file()
        and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        and not re.search(r"chatgpt|not[-_ ]?now", item.name, re.IGNORECASE)
    ]

    def sort_key(item: Path) -> tuple[int, int, str]:
        stem = item.stem
        if stem == "1": return (0, 1, item.name)
        if stem.upper().startswith("KLC-"): return (1, 0, item.name)
        if stem.isdigit(): return (2, int(stem), item.name)
        return (3, 0, item.name)

    return sorted(images, key=sort_key)[:8]


def stock_quantities(product_index: int) -> list[int]:
    # 27 products x 18 units, plus one extra unit for the first 14 products = 500.
    # The former 2-4Y quantity is split across the new 2-3Y and 3-4Y bands.
    quantities = [1, 1, 1, 3, 3, 3, 3, 3]
    if product_index < 14:
        quantities[-1] += 1
    return quantities


def write_csv(path: Path, rows: list[dict[str, str | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def build() -> None:
    rows: list[dict[str, str | int]] = []
    products_json: list[dict] = []
    media_rows: list[dict[str, str | int]] = []
    seen_skus: set[str] = set()

    for index, (relative_folder, base_sku, name, color, motif, category, mrp, sale_price) in enumerate(PRODUCTS):
        folder = INVENTORY_ROOT / INVENTORY_FOLDER_MAP.get(relative_folder, relative_folder)
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        slug = slugify(name)
        images = product_images(folder)
        if not images:
            raise ValueError(f"No launch images found for {relative_folder}")
        image_urls = []
        image_payloads = []
        for order, source in enumerate(images, start=1):
            key = f"catalog/{slug}/{order:02d}.webp"
            url = f"{CDN_BASE}/{slug}/{order:02d}.webp"
            image_urls.append(url)
            image_payloads.append({
                "image_url": url,
                "alt_text": f"{name} - view {order}",
                "display_order": order - 1,
                "is_primary": order == 1,
            })
            media_rows.append({
                "product_slug": slug,
                "display_order": order - 1,
                "is_primary": str(order == 1).lower(),
                "source_path": str(source),
                "r2_key": key,
                "public_url": url,
            })

        description = (
            f"A ready-to-wear South Indian lehenga choli set for girls in {color.lower()}, "
            f"finished with {motif.lower()} detailing and a traditional woven border. "
            "Designed for festivals, weddings, temple ceremonies and family celebrations. "
            "The set includes one choli and one lehenga."
        )
        fabric = "Art Silk Jacquard" if "Work" in relative_folder else "Silk Blend"
        collection = "AMZIRA Heritage Work" if "Work" in relative_folder else "AMZIRA Haresh Butta"
        common = {
            "product_slug": slug,
            "name": name,
            "category_slug": category,
            "subcategory_slug": "",
            "base_price": f"{mrp:.2f}",
            "sale_price": f"{sale_price:.2f}",
            "description": description,
            "fabric": fabric,
            "care_instructions": "Dry clean recommended. Store folded in a cool, dry place.",
            "meta_title": f"{name} for Girls | AMZIRA"[:100],
            "meta_description": f"Shop {name}, a ready-to-wear South Indian festive lehenga choli for girls aged 0-10 years."[:300],
            "audience": "kids_girls",
            "collection": collection,
            "tags": "girls-lehenga-choli|south-indian|pattu-pavadai|festive|ready-to-wear",
            "status": "active",
            "is_featured": str(index < 6).lower(),
            "is_bestseller": "false",
            "is_new_arrival": "true",
            "occasion_slugs": "festival|wedding|temple-ceremony|birthday",
            "image_urls": "|".join(image_urls),
            "external_source": "amzira_local_inventory",
            "external_id": relative_folder,
            "style_code": base_sku,
        }
        variants = []
        for (size_code, age_band), quantity in zip(SIZES, stock_quantities(index), strict=True):
            sku = f"{base_sku}-{size_code}"
            if sku in seen_skus:
                raise ValueError(f"Duplicate SKU: {sku}")
            seen_skus.add(sku)
            variant = {
                "sku": sku,
                "size": age_band,
                "color": color,
                "stock_quantity": quantity,
                "additional_price": "0.00",
                "is_active": True,
            }
            variants.append(variant)
            rows.append({
                **common,
                "sku": sku,
                "size": age_band,
                "color": color,
                "stock_quantity": quantity,
                "additional_price": "0.00",
                "variant_is_active": "true",
            })
        products_json.append({
            "name": name,
            "slug": slug,
            "category_slug": category,
            "subcategory_slug": None,
            "base_price": str(mrp),
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
            "is_featured": index < 6,
            "is_bestseller": False,
            "is_new_arrival": True,
            "occasion_slugs": common["occasion_slugs"].split("|"),
            "images": image_payloads,
            "variants": variants,
            "external_source": common["external_source"],
            "external_id": relative_folder,
            "style_code": base_sku,
        })

    total_stock = sum(int(row["stock_quantity"]) for row in rows)
    if len(PRODUCTS) != 27 or len(rows) != 216 or len(seen_skus) != 216 or total_stock != 500:
        raise AssertionError({"products": len(PRODUCTS), "variants": len(rows), "skus": len(seen_skus), "stock": total_stock})

    write_csv(OUTPUT_CSV, rows)
    OUTPUT_JSON.write_text(json.dumps({"products": products_json, "mode": "upsert", "dry_run": True}, indent=2), encoding="utf-8")
    local_rows = [
        {**row, "image_urls": str(row["image_urls"]).replace(CDN_BASE, LOCAL_MEDIA_BASE)}
        for row in rows
    ]
    local_products = json.loads(json.dumps(products_json).replace(CDN_BASE, LOCAL_MEDIA_BASE))
    write_csv(LOCAL_OUTPUT_CSV, local_rows)
    LOCAL_OUTPUT_JSON.write_text(json.dumps({"products": local_products, "mode": "upsert", "dry_run": True}, indent=2), encoding="utf-8")
    with MEDIA_MANIFEST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["product_slug", "display_order", "is_primary", "source_path", "r2_key", "public_url"])
        writer.writeheader()
        writer.writerows(media_rows)
    print(json.dumps({"products": 27, "variants": 216, "stock": total_stock, "media": len(media_rows), "csv": str(OUTPUT_CSV), "local_csv": str(LOCAL_OUTPUT_CSV)}))


if __name__ == "__main__":
    build()

import csv
import json
import logging
import zipfile
from io import BytesIO, StringIO
from defusedxml import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Body, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, EmailStr, ValidationError
from slugify import slugify
from app.db.session import get_db
from app.api.deps import require_admin
from app.models.user import User
from app.models.product import Product, ProductImage, ProductVariant, Occasion
from app.models.category import Category, Subcategory
from app.models.order import Order, OrderStatus
from app.schemas.product import ProductCreate
from app.schemas.catalog_import import CatalogImportRequest
from app.services.catalog_import_service import CatalogImportValidationError, import_catalog
from app.services.order_service import auto_cancel_pending_orders
from app.core.rate_limiter import limiter
from app.core.cache import invalidate_product_cache
from app.utils.image_upload import save_product_image, delete_product_image
from app.utils.response import success

router = APIRouter()
logger = logging.getLogger(__name__)
BULK_UPLOAD_HEADERS = [
    "name",
    "category_id",
    "subcategory_id",
    "base_price",
    "sale_price",
    "description",
    "fabric",
    "is_featured",
    "sizes",
    "colors",
    "stock",
    "additional_price",
    "image_urls",
    "occasion_slugs",
]
CATALOG_IMPORT_HEADERS = [
    "product_slug", "name", "category_slug", "subcategory_slug", "base_price",
    "sale_price", "description", "fabric", "care_instructions", "meta_title",
    "meta_description", "audience", "collection", "tags", "status", "is_featured",
    "is_bestseller", "is_new_arrival", "occasion_slugs", "image_urls", "sku",
    "size", "color", "stock_quantity", "additional_price", "variant_is_active",
    "external_source", "external_id", "style_code",
]
MAX_CATALOG_IMPORT_BYTES = 5 * 1024 * 1024
MAX_XLSX_ENTRIES = 100
MAX_XLSX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
MAX_XLSX_XML_BYTES = 2 * 1024 * 1024


class AdminTestEmailRequest(BaseModel):
    email: EmailStr


class BulkCategoryItem(BaseModel):
    name: str
    slug: Optional[str] = None
    display_order: int = 0
    description: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool = True


class BulkCategoryCreateRequest(BaseModel):
    categories: List[BulkCategoryItem]


class BulkProductCategoryUpdateRequest(BaseModel):
    product_ids: List[int]
    category_id: int


def _normalize_slug(value: str) -> str:
    normalized = slugify(value)
    if not normalized:
        raise HTTPException(status_code=400, detail="Slug cannot be empty")
    return normalized


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_csv_list(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _parse_optional_float(value: str | None) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return float(raw)


def _parse_optional_int(value: str | None, default: int = 0) -> int:
    raw = str(value or "").strip()
    if not raw:
        return default
    return int(raw)


def _parse_pipe_list(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def _catalog_validation_detail(exc: ValidationError) -> list[dict]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "code": error["type"],
            "message": error["msg"],
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def _catalog_request_from_csv(content: bytes, *, mode: str, dry_run: bool) -> CatalogImportRequest:
    try:
        rows = list(csv.DictReader(StringIO(content.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from exc
    if not rows:
        raise HTTPException(status_code=400, detail="Catalog CSV has no product rows")

    missing_headers = [header for header in ("product_slug", "name", "category_slug", "base_price", "sku", "size", "stock_quantity") if header not in (rows[0] or {})]
    if missing_headers:
        raise HTTPException(status_code=400, detail=f"Missing CSV headers: {', '.join(missing_headers)}")

    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for row_number, row in enumerate(rows, start=2):
        slug = str(row.get("product_slug") or "").strip()
        if not slug:
            raise HTTPException(status_code=422, detail={"message": "Catalog validation failed", "errors": [{"row": row_number, "field": "product_slug", "message": "Field is required"}]})
        grouped.setdefault(slug, []).append((row_number, row))

    products: list[dict] = []
    for product_slug, product_rows in grouped.items():
        first_row_number, first = product_rows[0]
        variants = []
        for row_number, row in product_rows:
            try:
                stock_quantity = int(str(row.get("stock_quantity") or "").strip())
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"message": "Catalog validation failed", "errors": [{"row": row_number, "field": "stock_quantity", "message": "Must be an integer"}]}) from exc
            variants.append(
                {
                    "sku": row.get("sku"),
                    "size": row.get("size"),
                    "color": row.get("color") or None,
                    "stock_quantity": stock_quantity,
                    "additional_price": row.get("additional_price") or "0",
                    "is_active": _parse_bool(row.get("variant_is_active")) if str(row.get("variant_is_active") or "").strip() else True,
                }
            )

        image_urls = _parse_pipe_list(first.get("image_urls"))
        products.append(
            {
                "name": first.get("name"),
                "slug": product_slug,
                "category_slug": first.get("category_slug"),
                "subcategory_slug": first.get("subcategory_slug") or None,
                "base_price": first.get("base_price"),
                "sale_price": first.get("sale_price") or None,
                "description": first.get("description") or None,
                "fabric": first.get("fabric") or None,
                "care_instructions": first.get("care_instructions") or None,
                "meta_title": first.get("meta_title") or None,
                "meta_description": first.get("meta_description") or None,
                "audience": first.get("audience") or "kids_girls",
                "collection": first.get("collection") or None,
                "tags": _parse_pipe_list(first.get("tags")),
                "status": first.get("status") or "active",
                "is_featured": _parse_bool(first.get("is_featured")),
                "is_bestseller": _parse_bool(first.get("is_bestseller")),
                "is_new_arrival": _parse_bool(first.get("is_new_arrival")),
                "occasion_slugs": _parse_pipe_list(first.get("occasion_slugs")),
                "images": [
                    {"image_url": url, "display_order": index, "is_primary": index == 0}
                    for index, url in enumerate(image_urls)
                ],
                "variants": variants,
                "external_source": first.get("external_source") or None,
                "external_id": first.get("external_id") or None,
                "style_code": first.get("style_code") or None,
                "_source_row": first_row_number,
            }
        )

    for product in products:
        product.pop("_source_row", None)
    try:
        return CatalogImportRequest(products=products, mode=mode, dry_run=dry_run)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": "Catalog validation failed", "errors": _catalog_validation_detail(exc)}) from exc


def _build_unique_product_slug(db: Session, name: str) -> str:
    base_slug = _normalize_slug(name)
    slug = base_slug
    suffix = 2
    while db.query(Product.id).filter(Product.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _safe_xlsx_xml_root(content: bytes):
    if len(content) > MAX_XLSX_XML_BYTES:
        raise HTTPException(status_code=400, detail="XLSX XML entry exceeds the 2 MB safety limit")
    upper_content = content.upper()
    if b"<!DOCTYPE" in upper_content or b"<!ENTITY" in upper_content:
        raise HTTPException(status_code=400, detail="XLSX contains prohibited XML declarations")
    return ET.fromstring(content)


def _extract_xlsx_rows(content: bytes) -> list[dict[str, str]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(content)) as workbook:
        entries = workbook.infolist()
        if len(entries) > MAX_XLSX_ENTRIES:
            raise HTTPException(status_code=400, detail="XLSX contains too many archive entries")
        total_uncompressed = sum(entry.file_size for entry in entries)
        if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise HTTPException(status_code=400, detail="XLSX exceeds the 10 MB uncompressed safety limit")
        if any(entry.file_size > 0 and entry.compress_size == 0 for entry in entries):
            raise HTTPException(status_code=400, detail="XLSX contains an invalid compressed entry")
        if any(entry.compress_size and entry.file_size / entry.compress_size > 100 for entry in entries):
            raise HTTPException(status_code=400, detail="XLSX compression ratio exceeds the safety limit")

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = _safe_xlsx_xml_root(workbook.read("xl/sharedStrings.xml"))
            for node in shared_root.findall("main:si", namespace):
                parts = [segment.text or "" for segment in node.findall(".//main:t", namespace)]
                shared_strings.append("".join(parts))

        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in workbook.namelist():
            raise HTTPException(status_code=400, detail="XLSX upload must include sheet1.xml")

        sheet_root = _safe_xlsx_xml_root(workbook.read(sheet_name))
        rows: list[list[str]] = []
        for row in sheet_root.findall(".//main:sheetData/main:row", namespace):
            values: list[str] = []
            for cell in row.findall("main:c", namespace):
                cell_type = cell.attrib.get("t")
                value_node = cell.find("main:v", namespace)
                inline_node = cell.find("main:is/main:t", namespace)
                value = ""
                if inline_node is not None and inline_node.text is not None:
                    value = inline_node.text
                elif value_node is not None and value_node.text is not None:
                    value = value_node.text
                    if cell_type == "s":
                        value = shared_strings[int(value)] if value.isdigit() and int(value) < len(shared_strings) else ""
                values.append(value)
            rows.append(values)

    if not rows:
        return []

    headers = [str(value or "").strip() for value in rows[0]]
    parsed_rows: list[dict[str, str]] = []
    for values in rows[1:]:
        if not any(str(value or "").strip() for value in values):
            continue
        parsed_rows.append(
            {
                headers[index]: str(values[index]).strip() if index < len(values) else ""
                for index in range(len(headers))
                if headers[index]
            }
        )
    return parsed_rows


def _extract_bulk_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    lowered = str(filename or "").lower()
    if lowered.endswith(".csv"):
        csv_data = StringIO(content.decode("utf-8-sig"))
        return list(csv.DictReader(csv_data))
    if lowered.endswith(".xlsx"):
        return _extract_xlsx_rows(content)
    raise HTTPException(status_code=400, detail="Bulk upload supports .csv and .xlsx files only")


def _attach_occasions_to_product(db: Session, product: Product, raw_value: str | None) -> None:
    occasion_slugs = _parse_csv_list(raw_value)
    if not occasion_slugs:
        return
    occasions = db.query(Occasion).filter(Occasion.slug.in_(occasion_slugs)).all()
    found = {occasion.slug for occasion in occasions}
    missing = [slug for slug in occasion_slugs if slug not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Invalid occasion_slugs: {', '.join(missing)}")
    product.occasions = occasions


def _create_bulk_variants(
    db: Session,
    *,
    product: Product,
    slug: str,
    sizes: list[str],
    colors: list[str],
    stock_quantity: int,
    additional_price: float,
) -> int:
    normalized_sizes = sizes or ["Free"]
    normalized_colors = colors or [""]
    created_count = 0
    for size in normalized_sizes:
        for color in normalized_colors:
            sku_parts = [slug, size]
            if color:
                sku_parts.append(color)
            db.add(
                ProductVariant(
                    product_id=product.id,
                    size=size,
                    color=color or None,
                    sku="-".join(sku_parts).upper().replace(" ", "-"),
                    stock_quantity=stock_quantity,
                    additional_price=additional_price,
                    is_active=True,
                )
            )
            created_count += 1
    return created_count


def _require_existing_category(db: Session, category_id: int) -> Category:
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail=f"Invalid category_id: {category_id}")
    return category


def _resolve_product_category(
    db: Session,
    category_id: int,
    subcategory_id: Optional[int] = None,
) -> tuple[int, Optional[int]]:
    category = _require_existing_category(db, category_id)
    if category.parent_id:
        if subcategory_id is not None:
            subcategory = db.query(Subcategory).filter(Subcategory.id == subcategory_id).first()
            if not subcategory:
                raise HTTPException(status_code=400, detail=f"Invalid subcategory_id: {subcategory_id}")
            return category.parent_id, subcategory_id

        legacy_subcategory = db.query(Subcategory).filter(Subcategory.slug == category.slug).first()
        if legacy_subcategory:
            return category.parent_id, legacy_subcategory.id

        # No legacy subcategory match: keep leaf category as the product category
        return category.id, None
    return category.id, subcategory_id


@router.post("/test-email")
@limiter.limit("10/minute")
def admin_test_email(
    request: Request,
    email: Optional[EmailStr] = Query(None),
    payload: Optional[AdminTestEmailRequest] = Body(None),
    current_admin: User = Depends(require_admin),
):
    """Admin: Queue a test email via Celery."""
    recipient = email or (payload.email if payload else None)
    if not recipient:
        raise HTTPException(status_code=400, detail="Email is required")

    subject = "Test Email from AMZIRA"
    body = "This is a test email from AMZIRA backend."
    html = """
    <html>
      <body>
        <h3>AMZIRA Test Email</h3>
        <p>This is a test email from the AMZIRA backend.</p>
      </body>
    </html>
    """

    try:
        from app.tasks.email_tasks import send_email_task
        send_email_task.delay(str(recipient), subject, body, html)
    except Exception:
        logger.exception("admin_test_email_queue_failed", email=str(recipient))
        raise HTTPException(status_code=500, detail="Failed to queue test email")

    return success(
        data={"email": str(recipient)},
        message="Test email queued successfully",
    )


@router.post("/maintenance/cleanup-expired-orders")
@limiter.limit("10/minute")
def cleanup_expired_orders_admin(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cleaned = auto_cancel_pending_orders(db)
    return success(
        data={"cleaned_orders": cleaned},
        message="Expired pending orders cleaned",
    )


# ============= PRODUCT MANAGEMENT =============

@router.post("/products", status_code=201)
@limiter.limit("30/minute")
def create_product(
    request: Request,
    name: str = Form(...),
    category_id: int = Form(...),
    description: Optional[str] = Form(None),
    base_price: float = Form(...),
    sale_price: Optional[float] = Form(None),
    fabric: Optional[str] = Form(None),
    care_instructions: Optional[str] = Form(None),
    is_featured: bool = Form(False),
    subcategory_id: Optional[int] = Form(None),
    occasion_ids: str = Form(""),  # Comma-separated IDs
    image_urls: Optional[str] = Form(None),  # Comma-separated URLs
    images: Optional[List[UploadFile]] = File(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create new product"""
    category_id, subcategory_id = _resolve_product_category(db, category_id, subcategory_id)

    # Generate slug
    slug = slugify(name)
    
    # Check if slug exists
    existing = db.query(Product).filter(Product.slug == slug).first()
    if existing:
        slug = f"{slug}-{db.query(Product).count() + 1}"
    
    # Calculate discount
    discount = 0
    if sale_price and sale_price < base_price:
        discount = int(((base_price - sale_price) / base_price) * 100)
    
    # Create product
    product = Product(
        name=name,
        slug=slug,
        category_id=category_id,
        subcategory_id=subcategory_id,
        description=description,
        base_price=base_price,
        sale_price=sale_price,
        discount_percentage=discount,
        fabric=fabric,
        care_instructions=care_instructions,
        is_featured=is_featured,
        is_active=True,
    )
    
    db.add(product)
    db.flush()  # Get product ID
    
    # Add occasions
    if occasion_ids:
        occ_ids = [int(id.strip()) for id in occasion_ids.split(",") if id.strip()]
        occasions = db.query(Occasion).filter(Occasion.id.in_(occ_ids)).all()
        product.occasions = occasions
    
    # Add images (either by URLs or uploads)
    if image_urls:
        url_list = [url.strip() for url in image_urls.split(",") if url.strip()]
        if not url_list:
            raise HTTPException(status_code=400, detail="image_urls must not be empty")
        for idx, image_url in enumerate(url_list):
            if not image_url.startswith("/static/"):
                raise HTTPException(status_code=400, detail="image_urls must start with /static/")
            product_image = ProductImage(
                product_id=product.id,
                image_url=image_url,
                alt_text=name,
                display_order=idx,
                is_primary=(idx == 0)
            )
            db.add(product_image)
    elif images:
        for idx, image_file in enumerate(images):
            image_url = save_product_image(image_file)
            
            product_image = ProductImage(
                product_id=product.id,
                image_url=image_url,
                alt_text=name,
                display_order=idx,
                is_primary=(idx == 0)
            )
            db.add(product_image)
    else:
        raise HTTPException(status_code=400, detail="Provide image_urls or images")
    
    db.commit()
    if created:
        invalidate_product_cache()
    db.refresh(product)
    invalidate_product_cache(slugs=[product.slug])
    
    return success(
        data={"product_id": product.id, "slug": product.slug},
        message="Product created successfully",
    )


@router.put("/products/{product_id}")
@limiter.limit("30/minute")
def update_product(
    request: Request,
    product_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    base_price: Optional[float] = Form(None),
    sale_price: Optional[float] = Form(None),
    fabric: Optional[str] = Form(None),
    is_featured: Optional[bool] = Form(None),
    is_active: Optional[bool] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update product"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if name:
        product.name = name
        product.slug = slugify(name)
    
    if description is not None:
        product.description = description
    
    if base_price is not None:
        product.base_price = base_price
    
    if sale_price is not None:
        product.sale_price = sale_price
        # Recalculate discount
        if sale_price and sale_price < product.base_price:
            product.discount_percentage = int(((product.base_price - sale_price) / product.base_price) * 100)
    
    if fabric is not None:
        product.fabric = fabric
    
    if is_featured is not None:
        product.is_featured = is_featured
    
    if is_active is not None:
        product.is_active = is_active
    
    db.commit()
    invalidate_product_cache(slugs=[product.slug])

    return success(message="Product updated successfully")


@router.delete("/products/{product_id}")
@limiter.limit("20/minute")
def delete_product(
    request: Request,
    product_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Delete product (soft delete)"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Soft delete (set inactive)
    product.is_active = False
    db.commit()
    invalidate_product_cache(slugs=[product.slug])

    return success(message="Product deleted successfully")


@router.put("/products/bulk-update-category")
@limiter.limit("20/minute")
def bulk_update_product_category(
    request: Request,
    payload: BulkProductCategoryUpdateRequest = Body(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Bulk reassign products to a category in one transaction."""
    if not payload.product_ids:
        raise HTTPException(status_code=400, detail="product_ids cannot be empty")

    product_ids = sorted(set(payload.product_ids))
    category_id, _ = _resolve_product_category(db, payload.category_id)

    existing_products = db.query(Product.id).filter(Product.id.in_(product_ids)).all()
    existing_ids = {product_id for (product_id,) in existing_products}
    missing_ids = sorted(set(product_ids) - existing_ids)
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Products not found: {missing_ids}")

    try:
        updated_count = db.query(Product).filter(Product.id.in_(product_ids)).update(
            {Product.category_id: category_id},
            synchronize_session=False,
        )
        db.commit()
        invalidate_product_cache()
    except Exception:
        db.rollback()
        raise

    return success(
        data={"updated_count": updated_count, "category_id": category_id},
        message="Products updated successfully",
    )


@router.post("/products/{product_id}/images")
@limiter.limit("30/minute")
async def add_product_images(
    request: Request,
    product_id: int,
    images: List[UploadFile] = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Add more images to product"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Get current max display order
    max_order = db.query(ProductImage).filter(
        ProductImage.product_id == product_id
    ).count()
    
    for idx, image_file in enumerate(images):
        image_url = save_product_image(image_file)
        
        product_image = ProductImage(
            product_id=product_id,
            image_url=image_url,
            alt_text=product.name,
            display_order=max_order + idx
        )
        db.add(product_image)
    
    db.commit()
    
    return success(message=f"{len(images)} images added successfully")


@router.delete("/products/images/{image_id}")
@limiter.limit("30/minute")
def delete_product_image_endpoint(
    request: Request,
    image_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Delete product image"""
    image = db.query(ProductImage).filter(ProductImage.id == image_id).first()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Delete file
    delete_product_image(image.image_url)
    
    # Delete from DB
    db.delete(image)
    db.commit()
    
    return success(message="Image deleted successfully")


# ============= VARIANT MANAGEMENT =============

@router.post("/products/{product_id}/variants")
@limiter.limit("30/minute")
def add_product_variant(
    request: Request,
    product_id: int,
    size: str = Form(...),
    color: Optional[str] = Form(None),
    stock_quantity: int = Form(...),
    additional_price: float = Form(0.0),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Add product variant"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Generate SKU
    sku = f"{product.slug[:10].upper()}-{size}"
    if color:
        sku += f"-{color[:3].upper()}"
    sku += f"-{db.query(ProductVariant).count() + 1}"
    
    variant = ProductVariant(
        product_id=product_id,
        size=size,
        color=color,
        sku=sku,
        stock_quantity=stock_quantity,
        additional_price=additional_price
    )
    
    db.add(variant)
    
    db.commit()
    invalidate_product_cache(slugs=[product.slug])

    return success(data={"sku": sku}, message="Variant added successfully")


@router.put("/variants/{variant_id}")
@limiter.limit("30/minute")
def update_variant_stock(
    request: Request,
    variant_id: int,
    stock_quantity: int = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update variant stock"""
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")
    
    variant.stock_quantity = stock_quantity
    
    db.commit()
    product_slug = variant.product.slug if variant.product else None
    invalidate_product_cache(slugs=[product_slug] if product_slug else None)

    return success(message="Stock updated successfully")


# ============= ORDER MANAGEMENT =============

@router.get("/orders")
@limiter.limit("60/minute")
def get_all_orders(
    request: Request,
    status: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get all orders"""
    query = db.query(Order)
    
    if status:
        query = query.filter(Order.status == status)
    
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    
    orders_response = []
    for order in orders:
        orders_response.append({
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.user.full_name,
            "customer_email": order.user.email,
            "status": order.status.value,
            "total_amount": order.total_amount,
            "items_count": len(order.items),
            "created_at": order.created_at,
            "tracking_number": order.tracking_number
        })
    
    return success(
        data={
            "total": total,
            "page": page,
            "limit": limit,
            "orders": orders_response,
        },
        message="Orders retrieved successfully",
    )


@router.get("/orders/{order_id}")
@limiter.limit("60/minute")
def get_order_detail_admin(
    request: Request,
    order_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get order details"""
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    items = [
        {
            "product_name": item.product_name,
            "variant_details": item.variant_details,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "total_price": item.total_price
        }
        for item in order.items
    ]
    
    return success(
        data={
            "id": order.id,
            "order_number": order.order_number,
            "customer": {
                "name": order.user.full_name,
                "email": order.user.email,
                "phone": order.user.phone,
            },
            "status": order.status.value,
            "subtotal": order.subtotal,
            "tax_amount": order.tax_amount,
            "shipping_charge": order.shipping_charge,
            "total_amount": order.total_amount,
            "items": items,
            "shipping_address": {
                "full_name": order.shipping_address.full_name,
                "phone": order.shipping_address.phone,
                "address_line1": order.shipping_address.address_line1,
                "address_line2": order.shipping_address.address_line2,
                "city": order.shipping_address.city,
                "state": order.shipping_address.state,
                "pincode": order.shipping_address.pincode,
            },
            "payment": {
                "method": order.payment.payment_method.value if order.payment else None,
                "status": order.payment.payment_status.value if order.payment else None,
            },
            "customer_notes": order.customer_notes,
            "admin_notes": order.admin_notes,
            "tracking_number": order.tracking_number,
            "created_at": order.created_at,
        },
        message="Order details retrieved successfully",
    )


@router.put(
    "/orders/{order_id}/status",
    summary="Update order status (admin)",
    description="""
Updates order status and optional tracking/admin note fields.

Behavior:
1. Validates order exists
2. Validates status value against allowed enum
3. Updates tracking number and admin notes if provided
4. Commits changes in one transaction
""",
    responses={
        200: {"description": "Order status updated successfully"},
        400: {"description": "Invalid status"},
        403: {"description": "Admin access required"},
        404: {"description": "Order not found"},
    },
    tags=["Admin"],
)
@limiter.limit("30/minute")
def update_order_status(
    request: Request,
    order_id: int,
    status: str = Form(...),
    tracking_number: Optional[str] = Form(None),
    admin_notes: Optional[str] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update order status"""
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Validate status
    try:
        order.status = OrderStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    if tracking_number:
        order.tracking_number = tracking_number
    
    if admin_notes:
        order.admin_notes = admin_notes
    
    db.commit()
    
    # TODO: Send email notification to customer
    
    return success(message="Order status updated successfully")


# ============= CATEGORY MANAGEMENT =============

@router.get("/categories")
@limiter.limit("60/minute")
def list_categories(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: List categories (active and inactive)."""
    categories = db.query(Category).order_by(Category.display_order.asc(), Category.id.asc()).all()
    return success(data=categories, message="Categories retrieved")


@router.post("/categories")
@limiter.limit("30/minute")
def create_category(
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create category"""
    slug = _normalize_slug(name)
    
    # Check if exists
    existing = db.query(Category).filter(Category.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Category already exists")
    
    category = Category(
        name=name,
        slug=slug,
        description=description
    )
    
    db.add(category)
    db.commit()
    
    return success(data={"id": category.id}, message="Category created")


@router.post("/categories/bulk")
@limiter.limit("10/minute")
def bulk_create_categories(
    request: Request,
    payload: BulkCategoryCreateRequest = Body(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Bulk create categories with atomic transaction."""
    if not payload.categories:
        raise HTTPException(status_code=400, detail="categories list is required")

    normalized_items = []
    duplicate_payload_slugs = set()
    duplicate_payload_names = set()
    seen_slugs = set()
    seen_names = set()

    for category in payload.categories:
        normalized_slug = _normalize_slug(category.slug or category.name)
        normalized_name = category.name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Category name cannot be empty")

        if normalized_slug in seen_slugs:
            duplicate_payload_slugs.add(normalized_slug)
        seen_slugs.add(normalized_slug)

        lower_name = normalized_name.lower()
        if lower_name in seen_names:
            duplicate_payload_names.add(normalized_name)
        seen_names.add(lower_name)

        normalized_items.append(
            {
                "name": normalized_name,
                "slug": normalized_slug,
                "display_order": category.display_order,
                "description": category.description,
                "image_url": category.image_url,
                "is_active": category.is_active,
            }
        )

    if duplicate_payload_slugs:
        duplicates = ", ".join(sorted(duplicate_payload_slugs))
        raise HTTPException(status_code=409, detail=f"Duplicate slugs in request: {duplicates}")
    if duplicate_payload_names:
        duplicates = ", ".join(sorted(duplicate_payload_names))
        raise HTTPException(status_code=409, detail=f"Duplicate names in request: {duplicates}")

    slugs = [item["slug"] for item in normalized_items]
    names = [item["name"] for item in normalized_items]
    existing_categories = db.query(Category).filter(
        (Category.slug.in_(slugs)) | (Category.name.in_(names))
    ).all()

    if existing_categories:
        existing_slugs = sorted({item.slug for item in existing_categories if item.slug in slugs})
        existing_names = sorted({item.name for item in existing_categories if item.name in names})
        details = []
        if existing_slugs:
            details.append(f"slugs: {', '.join(existing_slugs)}")
        if existing_names:
            details.append(f"names: {', '.join(existing_names)}")
        raise HTTPException(status_code=409, detail=f"Categories already exist ({'; '.join(details)})")

    categories = [
        Category(
            name=item["name"],
            slug=item["slug"],
            display_order=item["display_order"],
            description=item["description"],
            image_url=item["image_url"],
            is_active=item["is_active"],
        )
        for item in normalized_items
    ]

    try:
        for category in categories:
            db.add(category)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return success(
        data={
            "created_count": len(categories),
            "categories": [{"id": category.id, "name": category.name, "slug": category.slug} for category in categories],
        },
        message="Categories created successfully",
    )


@router.put("/categories/{category_id}")
@limiter.limit("30/minute")
def update_category(
    request: Request,
    category_id: int,
    name: Optional[str] = Form(None),
    slug: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    display_order: Optional[int] = Form(None),
    is_active: Optional[bool] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Update category fields and enforce slug uniqueness."""
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Category name cannot be empty")
        duplicate_name = db.query(Category).filter(
            Category.id != category_id,
            Category.name == normalized_name,
        ).first()
        if duplicate_name:
            raise HTTPException(status_code=409, detail="Category name already exists")
        category.name = normalized_name

    slug_source = None
    if slug is not None:
        slug_source = slug
    elif name is not None:
        slug_source = name

    if slug_source is not None:
        normalized_slug = _normalize_slug(slug_source)
        duplicate_slug = db.query(Category).filter(
            Category.id != category_id,
            Category.slug == normalized_slug,
        ).first()
        if duplicate_slug:
            raise HTTPException(status_code=409, detail="Category slug already exists")
        category.slug = normalized_slug

    if description is not None:
        category.description = description
    if image_url is not None:
        category.image_url = image_url
    if display_order is not None:
        category.display_order = display_order
    if is_active is not None:
        category.is_active = is_active

    db.commit()
    db.refresh(category)

    return success(data={"id": category.id, "slug": category.slug}, message="Category updated")


@router.delete("/categories/{category_id}")
@limiter.limit("20/minute")
def delete_category(
    request: Request,
    category_id: int,
    hard_delete: bool = Query(False),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Deactivate or delete a category if no products are linked."""
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    existing_product = db.query(Product.id).filter(Product.category_id == category_id).first()
    if existing_product:
        raise HTTPException(status_code=409, detail="Cannot delete category while products are assigned")

    if hard_delete:
        db.delete(category)
        db.commit()
        return success(message="Category deleted")

    category.is_active = False
    db.commit()
    return success(message="Category deactivated")


@router.post("/occasions")
@limiter.limit("30/minute")
def create_occasion(
    request: Request,
    name: str = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create occasion"""
    slug = slugify(name)
    
    # Check if exists
    existing = db.query(Occasion).filter(Occasion.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Occasion already exists")
    
    occasion = Occasion(name=name, slug=slug)
    db.add(occasion)
    db.commit()
    
    return success(data={"id": occasion.id}, message="Occasion created")


# ============= ANALYTICS =============

@router.get("/analytics")
@limiter.limit("60/minute")
def get_analytics(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get analytics dashboard"""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    # Total orders
    total_orders = db.query(Order).count()
    
    # Pending orders
    pending_orders = db.query(Order).filter(Order.status.in_([OrderStatus.PLACED, OrderStatus.PENDING])).count()
    
    # Total revenue
    total_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # Today's revenue
    today_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= today,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # This week's revenue
    week_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= week_ago,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # This month's revenue
    month_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= month_ago,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # Top selling products
    from app.models.order import OrderItem
    top_products = db.query(
        OrderItem.product_name,
        func.sum(OrderItem.quantity).label('total_sold')
    ).group_by(OrderItem.product_name).order_by(func.sum(OrderItem.quantity).desc()).limit(5).all()
    
    return success(
        data={
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "total_revenue": total_revenue,
            "today_revenue": today_revenue,
            "week_revenue": week_revenue,
            "month_revenue": month_revenue,
            "top_products": [{"name": p[0], "sold": p[1]} for p in top_products],
        },
        message="Analytics retrieved successfully",
    )





@router.get("/inventory/overview")
@limiter.limit("60/minute")
def get_inventory_overview(
    request: Request,
    low_stock_threshold: int = Query(5, ge=0, le=100),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = request
    total_products = db.query(Product).count()
    active_products = db.query(Product).filter(Product.is_active == True).count()
    total_variants = db.query(ProductVariant).count()
    out_of_stock_variants = db.query(ProductVariant).filter(ProductVariant.stock_quantity <= 0).count()
    low_stock_variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.stock_quantity > 0, ProductVariant.stock_quantity <= low_stock_threshold)
        .count()
    )
    low_stock_products = (
        db.query(
            Product.id,
            Product.name,
            Product.slug,
            func.sum(ProductVariant.stock_quantity).label("total_stock"),
        )
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .group_by(Product.id)
        .having(func.sum(ProductVariant.stock_quantity) > 0)
        .having(func.sum(ProductVariant.stock_quantity) <= low_stock_threshold)
        .order_by(func.sum(ProductVariant.stock_quantity).asc(), Product.id.asc())
        .limit(10)
        .all()
    )
    return success(
        data={
            "total_products": total_products,
            "active_products": active_products,
            "total_variants": total_variants,
            "out_of_stock_variants": out_of_stock_variants,
            "low_stock_variants": low_stock_variants,
            "low_stock_threshold": low_stock_threshold,
            "low_stock_products": [
                {
                    "product_id": product_id,
                    "name": name,
                    "slug": slug,
                    "total_stock": int(total_stock or 0),
                }
                for product_id, name, slug, total_stock in low_stock_products
            ],
        },
        message="Inventory overview retrieved successfully",
    )


@router.get("/products/catalog-import/template")
@limiter.limit("20/minute")
def catalog_import_template(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    _ = request
    return success(
        data={
            "format": "One CSV row per exact SKU. Separate list values with |.",
            "headers": CATALOG_IMPORT_HEADERS,
            "sample_rows": [
                {
                    "product_slug": "ruby-pattu-pavadai",
                    "name": "Ruby Pattu Pavadai",
                    "category_slug": "pattu-pavadai",
                    "subcategory_slug": "",
                    "base_price": "3499",
                    "sale_price": "3199",
                    "description": "South Indian festive lehenga set for girls",
                    "fabric": "Art Silk",
                    "care_instructions": "Dry clean only",
                    "meta_title": "Ruby Pattu Pavadai for Girls",
                    "meta_description": "Shop a festive South Indian pattu pavadai for girls.",
                    "audience": "kids_girls",
                    "collection": "Festive 2026",
                    "tags": "south-indian|festive|girls",
                    "status": "active",
                    "is_featured": "true",
                    "is_bestseller": "false",
                    "is_new_arrival": "true",
                    "occasion_slugs": "festive|wedding",
                    "image_urls": "https://cdn.amzira.com/products/ruby-front.jpg|https://cdn.amzira.com/products/ruby-back.jpg",
                    "sku": "AMZ-RUBY-24-RD",
                    "size": "24",
                    "color": "Ruby Red",
                    "stock_quantity": "4",
                    "additional_price": "0",
                    "variant_is_active": "true",
                    "external_source": "myntra",
                    "external_id": "STYLE-001",
                    "style_code": "ETHZY-001",
                }
            ],
        },
        message="Production catalog import template retrieved",
    )


@router.post("/products/catalog-import/json")
@limiter.limit("5/10 minutes")
def catalog_import_json(
    request: Request,
    payload: CatalogImportRequest,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = request
    try:
        report = import_catalog(db, payload)
    except CatalogImportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Catalog validation failed; no records were changed", "errors": exc.errors},
        ) from exc
    if not payload.dry_run:
        invalidate_product_cache(slugs=report.get("changed_slugs"))
    return success(data=report, message="Catalog dry run passed" if payload.dry_run else "Catalog imported atomically")


@router.post("/products/catalog-import")
@limiter.limit("5/10 minutes")
async def catalog_import_file(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Query("create", pattern="^(create|upsert)$"),
    dry_run: bool = Query(False),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = request
    content = await file.read(MAX_CATALOG_IMPORT_BYTES + 1)
    if len(content) > MAX_CATALOG_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Catalog import exceeds the 5 MB limit")

    filename = str(file.filename or "").lower()
    if filename.endswith(".csv"):
        payload = _catalog_request_from_csv(content, mode=mode, dry_run=dry_run)
    elif filename.endswith(".json"):
        try:
            raw_payload = json.loads(content.decode("utf-8"))
            if isinstance(raw_payload, list):
                raw_payload = {"products": raw_payload}
            raw_payload["mode"] = mode
            raw_payload["dry_run"] = dry_run
            payload = CatalogImportRequest.model_validate(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValidationError) as exc:
            errors = _catalog_validation_detail(exc) if isinstance(exc, ValidationError) else [{"field": "file", "code": "invalid_json", "message": str(exc)}]
            raise HTTPException(status_code=422, detail={"message": "Catalog validation failed", "errors": errors}) from exc
    else:
        raise HTTPException(status_code=400, detail="Production catalog import supports .csv and .json files")

    try:
        report = import_catalog(db, payload)
    except CatalogImportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Catalog validation failed; no records were changed", "errors": exc.errors},
        ) from exc
    if not dry_run:
        invalidate_product_cache(slugs=report.get("changed_slugs"))
    return success(data=report, message="Catalog dry run passed" if dry_run else "Catalog imported atomically")


@router.get("/products/bulk-upload/template", deprecated=True)
@limiter.limit("20/minute")
def download_bulk_upload_template(
    request: Request,
    current_admin: User = Depends(require_admin),
):
    _ = request
    return success(
        data={
            "headers": BULK_UPLOAD_HEADERS,
            "sample_row": {
                "name": "Ivory Sherwani",
                "category_id": "1",
                "subcategory_id": "",
                "base_price": "6999",
                "sale_price": "6499",
                "description": "Festive sherwani with woven detailing",
                "fabric": "Silk Blend",
                "is_featured": "true",
                "sizes": "M,L,XL",
                "colors": "Ivory,Gold",
                "stock": "8",
                "additional_price": "0",
                "image_urls": "https://cdn.amzira.test/ivory-front.jpg,https://cdn.amzira.test/ivory-back.jpg",
                "occasion_slugs": "wedding,reception",
            },
        },
        message="Bulk upload template retrieved successfully",
    )


# app/api/v1/admin.py

@router.post("/products/bulk-upload", deprecated=True)
@limiter.limit("10/minute")
async def bulk_upload_products(
    request: Request,
    file: UploadFile = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Bulk upload products from CSV or XLSX."""
    content = await file.read(MAX_CATALOG_IMPORT_BYTES + 1)
    if len(content) > MAX_CATALOG_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Bulk upload exceeds the 5 MB limit")
    rows = _extract_bulk_rows(file.filename or "", content)

    created = []
    errors = []
    category_exists_cache = {}
    created_variants = 0

    for row_number, row in enumerate(rows, start=2):
        try:
            # Validate required fields
            required = ['name', 'category_id', 'base_price']
            missing = [f for f in required if not row.get(f)]
            if missing:
                errors.append(f"Row {row_number}: Missing {missing}")
                continue

            try:
                category_id = int(row["category_id"])
            except ValueError:
                errors.append(f"Row {row_number}: category_id must be an integer")
                continue

            if category_id not in category_exists_cache:
                category_exists_cache[category_id] = (
                    db.query(Category.id).filter(Category.id == category_id).first() is not None
                )
            if not category_exists_cache[category_id]:
                errors.append(f"Row {row_number}: Invalid category_id {category_id}")
                continue

            subcategory_id = _parse_optional_int(row.get("subcategory_id"), default=0) or None
            resolved_category_id, resolved_subcategory_id = _resolve_product_category(
                db,
                category_id,
                subcategory_id,
            )

            with db.begin_nested():
                slug = _build_unique_product_slug(db, row['name'])
                sale_price = _parse_optional_float(row.get('sale_price'))
                base_price = float(row['base_price'])
                discount = 0
                if sale_price is not None and sale_price < base_price:
                    discount = int(((base_price - sale_price) / base_price) * 100)
                product = Product(
                    name=row['name'],
                    slug=slug,
                    category_id=resolved_category_id,
                    subcategory_id=resolved_subcategory_id,
                    base_price=base_price,
                    sale_price=sale_price,
                    discount_percentage=discount,
                    description=row.get('description'),
                    fabric=row.get('fabric'),
                    is_featured=_parse_bool(row.get('is_featured')),
                    is_active=True,
                )
                db.add(product)
                db.flush()

                _attach_occasions_to_product(db, product, row.get("occasion_slugs"))

                image_urls = _parse_csv_list(row.get("image_urls"))
                for image_index, image_url in enumerate(image_urls):
                    db.add(
                        ProductImage(
                            product_id=product.id,
                            image_url=image_url,
                            alt_text=product.name,
                            display_order=image_index,
                            is_primary=(image_index == 0),
                        )
                    )

                created_variants += _create_bulk_variants(
                    db,
                    product=product,
                    slug=slug,
                    sizes=_parse_csv_list(row.get("sizes")),
                    colors=_parse_csv_list(row.get("colors")),
                    stock_quantity=_parse_optional_int(row.get("stock"), default=0),
                    additional_price=_parse_optional_float(row.get("additional_price")) or 0.0,
                )

            created.append(product.name)
        except Exception as e:
            errors.append(f"Row {row_number}: {str(e)}")
    
    db.commit()
    
    return success(
        data={
            "created_count": len(created),
            "created_variant_count": created_variants,
            "error_count": len(errors),
            "created": created[:10],
            "errors": errors[:10],
            "rows_processed": len(rows),
        },
        message="Bulk upload completed",
    )


@router.get("/orders/export")
@limiter.limit("20/minute")
def export_orders(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Export orders to CSV"""
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    
    query = db.query(Order)
    
    if start_date:
        query = query.filter(Order.created_at >= start_date)
    if end_date:
        query = query.filter(Order.created_at <= end_date)
    if status:
        query = query.filter(Order.status == status)
    
    orders = query.all()
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'Order Number', 'Date', 'Customer', 'Email', 'Status',
        'Items', 'Total', 'Payment Method', 'Tracking'
    ])
    
    # Data
    for order in orders:
        writer.writerow([
            order.order_number,
            order.created_at.strftime('%Y-%m-%d %H:%M'),
            order.user.full_name,
            order.user.email,
            order.status.value,
            len(order.items),
            order.total_amount,
            order.payment.payment_method.value if order.payment else '',
            order.tracking_number or ''
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"}
    )

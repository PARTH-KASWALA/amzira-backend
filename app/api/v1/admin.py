import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Body, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from slugify import slugify
from app.db.session import get_db
from app.api.deps import require_admin
from app.models.user import User
from app.models.product import Product, ProductImage, ProductVariant, Occasion
from app.models.category import Category, Subcategory
from app.models.order import Order, OrderStatus
from app.schemas.product import ProductCreate
from app.services.order_service import auto_cancel_pending_orders
from app.core.rate_limiter import limiter
from app.utils.image_upload import save_product_image, delete_product_image
from app.utils.response import success

router = APIRouter()
logger = logging.getLogger(__name__)


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
        is_featured=is_featured
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
    db.refresh(product)
    
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
    
    # Update product total stock
    product.total_stock = sum(v.stock_quantity for v in product.variants) + stock_quantity
    
    db.commit()
    
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
    
    # Update product total stock
    product = variant.product
    product.total_stock = sum(v.stock_quantity for v in product.variants)
    
    db.commit()
    
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
    pending_orders = db.query(Order).filter(Order.status == OrderStatus.PENDING).count()
    
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





# app/api/v1/admin.py

@router.post("/products/bulk-upload")
@limiter.limit("10/minute")
async def bulk_upload_products(
    request: Request,
    file: UploadFile = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Bulk upload products from CSV"""
    import csv
    from io import StringIO
    
    content = await file.read()
    csv_data = StringIO(content.decode('utf-8'))
    reader = csv.DictReader(csv_data)
    
    created = []
    errors = []
    category_exists_cache = {}
    
    for row in reader:
        row_number = reader.line_num
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

            resolved_category_id, _ = _resolve_product_category(db, category_id)

            with db.begin_nested():
                slug = slugify(row['name'])
                product = Product(
                    name=row['name'],
                    slug=slug,
                    category_id=resolved_category_id,
                    base_price=float(row['base_price']),
                    sale_price=float(row['sale_price']) if row.get('sale_price') else None,
                    description=row.get('description'),
                    fabric=row.get('fabric'),
                    is_featured=row.get('is_featured', '').lower() == 'true'
                )
                db.add(product)
                db.flush()

                # Add variants if provided
                if row.get('sizes'):
                    sizes = row['sizes'].split(',')
                    for size in sizes:
                        variant = ProductVariant(
                            product_id=product.id,
                            size=size.strip(),
                            sku=f"{slug}-{size.strip()}".upper(),
                            stock_quantity=int(row.get('stock', 0))
                        )
                        db.add(variant)

            created.append(product.name)
        except Exception as e:
            errors.append(f"Row {row_number}: {str(e)}")
    
    db.commit()
    
    return success(
        data={
            "created_count": len(created),
            "error_count": len(errors),
            "created": created[:10],
            "errors": errors[:10],
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

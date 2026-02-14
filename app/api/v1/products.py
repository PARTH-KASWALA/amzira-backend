from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import or_, and_
from typing import Optional, List
from datetime import datetime, timedelta
import re
from app.db.session import get_db
from app.models.product import Product, ProductImage, ProductVariant, Occasion
from app.models.category import Category, Subcategory
from app.schemas.product import ProductListResponse, ProductDetailResponse, CategoryResponse
from app.core.exceptions import ProductNotFound
from app.utils.response import success
from app.core.rate_limiter import limiter

router = APIRouter()
PINCODE_RE = re.compile(r"^\d{6}$")
FREE_SHIPPING_THRESHOLD = 2000.0
DEFAULT_SHIPPING_CHARGE = 100.0


@router.get("/categories", response_model=dict)
@limiter.limit("100/minute")
def get_categories(request: Request, db: Session = Depends(get_db)):
    """Get all active categories"""
    categories = db.query(Category).filter(Category.is_active == True).order_by(Category.display_order).all()
    return success(data=categories, message="Categories retrieved")


@router.get("", response_model=dict)
@router.get("/", response_model=dict)
@limiter.limit("100/minute")
def get_products(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    occasion: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    search: Optional[str] = None,
    featured: Optional[bool] = None,
    sort_by: Optional[str] = Query(None, regex="^(price_asc|price_desc|newest|popular)$"),
    db: Session = Depends(get_db)
):
    """
    Get products with filtering and pagination
    """
    query = (
        db.query(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.images),
            selectinload(Product.variants),
        )
        .filter(Product.is_active == True)
    )
    
    # Category filter
    if category:
        cat = db.query(Category).filter(Category.slug == category).first()
        if cat:
            query = query.filter(Product.category_id == cat.id)
    
    # Subcategory filter
    if subcategory:
        subcat = db.query(Subcategory).filter(Subcategory.slug == subcategory).first()
        if subcat:
            query = query.filter(Product.subcategory_id == subcat.id)
    
    # Occasion filter
    if occasion:
        occ = db.query(Occasion).filter(Occasion.slug == occasion).first()
        if occ:
            query = query.filter(Product.occasions.contains(occ))
    
    # Price range filter
    if min_price is not None:
        query = query.filter(
            or_(
                Product.sale_price >= min_price,
                and_(Product.sale_price == None, Product.base_price >= min_price)
            )
        )
    
    if max_price is not None:
        query = query.filter(
            or_(
                Product.sale_price <= max_price,
                and_(Product.sale_price == None, Product.base_price <= max_price)
            )
        )
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Product.name.ilike(search_term),
                Product.description.ilike(search_term)
            )
        )
    
    # Featured filter
    if featured:
        query = query.filter(Product.is_featured == True)
    
    # Sorting
    if sort_by == "price_asc":
        query = query.order_by(Product.sale_price.asc().nullslast(), Product.base_price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Product.sale_price.desc().nullsfirst(), Product.base_price.desc())
    elif sort_by == "newest":
        query = query.order_by(Product.created_at.desc())
    else:
        query = query.order_by(Product.id.desc())
    
    # Pagination
    total = query.count()
    products = query.offset((page - 1) * limit).limit(limit).all()
    
    # Format response
    products_list = []
    for product in products:
        primary_image = next((img.image_url for img in product.images if img.is_primary), None)
        if not primary_image and product.images:
            primary_image = product.images[0].image_url
        
        active_variants = [variant for variant in product.variants if variant.is_active]
        in_stock_variants = sorted(
            [variant for variant in active_variants if variant.stock_quantity > 0],
            key=lambda variant: variant.id,
        )
        default_variant = None
        if in_stock_variants:
            chosen = in_stock_variants[0]
            default_variant = {
                "variant_id": chosen.id,
                "size": chosen.size,
                "color": chosen.color,
                "stock_quantity": chosen.stock_quantity,
            }

        stock_quantity = sum(v.stock_quantity for v in active_variants)
        in_stock = stock_quantity > 0
        
        products_list.append({
            "id": product.id,
            "name": product.name,
            "slug": product.slug,
            "base_price": product.base_price,
            "sale_price": product.sale_price,
            "discount_percentage": product.discount_percentage,
            "is_featured": product.is_featured,
            "stock_quantity": stock_quantity,
            "default_variant": default_variant,
            "category": {
                "id": product.category.id,
                "name": product.category.name,
                "slug": product.category.slug
            },
            "primary_image": primary_image,
            "in_stock": in_stock
        })
    
    return success(
        data={
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "products": products_list,
        },
        message="Products retrieved",
    )


@router.get("/category/{category_slug}")
@limiter.limit("100/minute")
def get_products_by_category(
    request: Request,
    category_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get products by category slug"""
    return get_products(request=request, page=page, limit=limit, category=category_slug, db=db)


@router.get("/occasion/{occasion_slug}")
@limiter.limit("100/minute")
def get_products_by_occasion(
    request: Request,
    occasion_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get products by occasion slug"""
    return get_products(request=request, page=page, limit=limit, occasion=occasion_slug, db=db)


@router.get("/{slug}", response_model=dict)
@limiter.limit("100/minute")
def get_product_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    """Get product details by slug."""
    product = (
        db.query(Product)
        .options(
            joinedload(Product.images),
            joinedload(Product.category),
            selectinload(Product.variants),
            selectinload(Product.occasions),
        )
        .filter(
            Product.slug == slug,
            Product.is_active == True
        )
        .first()
    )

    if not product:
        raise ProductNotFound()

    primary_image = next((img.image_url for img in product.images if img.is_primary), None)
    if not primary_image and product.images:
        primary_image = product.images[0].image_url

    in_stock = any(v.stock_quantity > 0 for v in product.variants)

    images = [
        {
            "id": img.id,
            "image_url": img.image_url,
            "alt_text": img.alt_text,
            "display_order": img.display_order,
            "is_primary": img.is_primary
        }
        for img in sorted(product.images, key=lambda x: (not x.is_primary, x.display_order))
    ]

    variants = [
        {
            "id": v.id,
            "size": v.size,
            "color": v.color,
            "sku": v.sku,
            "stock_quantity": v.stock_quantity,
            "additional_price": v.additional_price,
            "is_active": v.is_active
        }
        for v in product.variants if v.is_active
    ]

    occasions = [
        {
            "id": occ.id,
            "name": occ.name,
            "slug": occ.slug
        }
        for occ in product.occasions
    ]

    return success(
        data={
            "id": product.id,
            "name": product.name,
            "slug": product.slug,
            "description": product.description,
            "base_price": product.base_price,
            "sale_price": product.sale_price,
            "discount_percentage": product.discount_percentage,
            "is_featured": product.is_featured,
            "total_stock": product.total_stock,
            "avg_rating": product.avg_rating,
            "review_count": product.review_count,
            "fabric": product.fabric,
            "care_instructions": product.care_instructions,
            "category": {
                "id": product.category.id,
                "name": product.category.name,
                "slug": product.category.slug,
            },
            "primary_image": primary_image,
            "in_stock": in_stock,
            "images": images,
            "variants": variants,
            "occasions": occasions,
            "created_at": product.created_at,
        },
        message="Product retrieved",
    )


@router.get("/{slug}/delivery-estimate", response_model=dict)
@limiter.limit("60/minute")
def get_product_delivery_estimate(
    request: Request,
    slug: str,
    pincode: str = Query(..., description="6 digit Indian pincode"),
    db: Session = Depends(get_db),
):
    """Estimate shipping SLA and COD availability for a PDP pincode check."""
    if not PINCODE_RE.match(str(pincode or "").strip()):
        raise HTTPException(status_code=400, detail="Pincode must be 6 digits")

    product = (
        db.query(Product)
        .filter(
            Product.slug == slug,
            Product.is_active == True,
        )
        .first()
    )
    if not product:
        raise ProductNotFound()

    current_price = product.sale_price if product.sale_price is not None else product.base_price
    shipping_cost = 0.0 if current_price >= FREE_SHIPPING_THRESHOLD else DEFAULT_SHIPPING_CHARGE

    first_digit = pincode[0]
    if first_digit in {"1", "2", "3", "4"}:
        min_days, max_days, cod_available = 2, 4, True
    elif first_digit in {"5", "6"}:
        min_days, max_days, cod_available = 4, 6, True
    elif first_digit in {"7", "8"}:
        min_days, max_days, cod_available = 5, 7, True
    else:
        min_days, max_days, cod_available = 6, 8, False

    today = datetime.utcnow().date()
    return success(
        data={
            "pincode": pincode,
            "cod_available": cod_available,
            "shipping_cost": shipping_cost,
            "delivery_days_min": min_days,
            "delivery_days_max": max_days,
            "estimated_delivery_date_start": (today + timedelta(days=min_days)).isoformat(),
            "estimated_delivery_date_end": (today + timedelta(days=max_days)).isoformat(),
        },
        message="Delivery estimate retrieved",
    )

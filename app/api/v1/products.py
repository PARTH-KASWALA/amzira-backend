from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
import re
import logging
from app.db.session import get_db
from app.models.product import Product
from app.models.category import Category, Subcategory
from app.schemas.product import ProductListResponse, ProductDetailResponse, CategoryResponse
from app.utils.response import success
from app.core.rate_limiter import limiter
from app.services.product_service import (
    get_product_delivery_estimate as service_get_product_delivery_estimate,
    get_product_detail as service_get_product_detail,
    list_products as service_list_products,
)

router = APIRouter()
PINCODE_RE = re.compile(r"^\d{6}$")
FREE_SHIPPING_THRESHOLD = 2000.0
DEFAULT_SHIPPING_CHARGE = 100.0
logger = logging.getLogger(__name__)


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
    category_id: Optional[int] = Query(None, ge=1),
    subcategory_id: Optional[int] = Query(None, ge=1),
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    occasion: Optional[str] = None,
    fabric: Optional[str] = None,
    color: Optional[str] = None,
    size: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    search: Optional[str] = None,
    featured: Optional[bool] = None,
    is_featured: Optional[bool] = Query(None, alias="is_featured"),
    in_stock_only: Optional[bool] = Query(None, alias="in_stock_only"),
    sort_by: Optional[str] = Query(None, regex="^(price_asc|price_desc|newest|popular)$"),
    db: Session = Depends(get_db)
):
    """
    Get products with filtering and pagination
    """
    data = service_list_products(
        db,
        request,
        page=page,
        limit=limit,
        category_id=category_id,
        subcategory_id=subcategory_id,
        category=category,
        subcategory=subcategory,
        occasion=occasion,
        fabric=fabric,
        color=color,
        size=size,
        min_price=min_price,
        max_price=max_price,
        search=search,
        featured=featured,
        is_featured=is_featured,
        in_stock_only=in_stock_only,
        sort_by=sort_by,
    )
    response = success(
        data=data,
        message="Products retrieved",
    )
    return response


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
    response = success(
        data=service_get_product_detail(db, slug=slug),
        message="Product retrieved",
    )
    return response


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

    return success(
        data=service_get_product_delivery_estimate(
            db,
            slug=slug,
            pincode=pincode,
            free_shipping_threshold=FREE_SHIPPING_THRESHOLD,
            default_shipping_charge=DEFAULT_SHIPPING_CHARGE,
        ),
        message="Delivery estimate retrieved",
    )

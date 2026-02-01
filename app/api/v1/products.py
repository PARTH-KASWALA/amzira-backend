from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import Optional, List
from app.db.session import get_db
from app.models.product import Product, ProductImage, ProductVariant, Occasion
from app.models.category import Category, Subcategory
from app.schemas.product import ProductListResponse, ProductDetailResponse, CategoryResponse
from app.core.exceptions import ProductNotFound

router = APIRouter()


@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(db: Session = Depends(get_db)):
    """Get all active categories"""
    categories = db.query(Category).filter(Category.is_active == True).order_by(Category.display_order).all()
    return categories


@router.get("/", response_model=dict)
def get_products(
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
    query = db.query(Product).filter(Product.is_active == True)
    
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
        
        # Check stock
        in_stock = any(v.stock_quantity > 0 for v in product.variants)
        
        products_list.append({
            "id": product.id,
            "name": product.name,
            "slug": product.slug,
            "base_price": product.base_price,
            "sale_price": product.sale_price,
            "discount_percentage": product.discount_percentage,
            "is_featured": product.is_featured,
            "category": {
                "id": product.category.id,
                "name": product.category.name,
                "slug": product.category.slug
            },
            "primary_image": primary_image,
            "in_stock": in_stock
        })
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
        "products": products_list
    }


@router.get("/{slug}", response_model=dict)
def get_product_detail(slug: str, db: Session = Depends(get_db)):
    """Get product details by slug"""
    product = db.query(Product).filter(
        Product.slug == slug,
        Product.is_active == True
    ).first()
    
    if not product:
        raise ProductNotFound()
    
    # Get primary image
    primary_image = next((img.image_url for img in product.images if img.is_primary), None)
    if not primary_image and product.images:
        primary_image = product.images[0].image_url
    
    # Check stock
    in_stock = any(v.stock_quantity > 0 for v in product.variants)
    
    # Format images
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
    
    # Format variants
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
    
    # Format occasions
    occasions = [
        {
            "id": occ.id,
            "name": occ.name,
            "slug": occ.slug
        }
        for occ in product.occasions
    ]
    
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "base_price": product.base_price,
        "sale_price": product.sale_price,
        "discount_percentage": product.discount_percentage,
        "is_featured": product.is_featured,
        "fabric": product.fabric,
        "care_instructions": product.care_instructions,
        "category": {
            "id": product.category.id,
            "name": product.category.name,
            "slug": product.category.slug
        },
        "primary_image": primary_image,
        "in_stock": in_stock,
        "images": images,
        "variants": variants,
        "occasions": occasions,
        "created_at": product.created_at
    }


@router.get("/category/{category_slug}")
def get_products_by_category(
    category_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get products by category slug"""
    return get_products(page=page, limit=limit, category=category_slug, db=db)


@router.get("/occasion/{occasion_slug}")
def get_products_by_occasion(
    occasion_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get products by occasion slug"""
    return get_products(page=page, limit=limit, occasion=occasion_slug, db=db)
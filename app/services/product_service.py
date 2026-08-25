from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import settings
from app.core.exceptions import ProductNotFound
from app.models.category import Category, Subcategory
from app.models.product import Occasion, Product, ProductVariant

PRODUCT_COUNT_CACHE_TTL_SECONDS = 60
PRODUCT_LIST_CACHE_TTL_SECONDS = 60
PRODUCT_DETAIL_CACHE_TTL_SECONDS = 120


def _is_front_view(image) -> bool:
    """Return whether catalog metadata identifies an image as the front view."""
    label = f"{image.alt_text or ''} {image.image_url or ''}".lower()
    return bool(re.search(r"(?:^|[^a-z])front(?:[^a-z]|$)", label))


def _ordered_product_images(images):
    """Keep front view first, then honor the explicit primary/display order metadata."""
    return sorted(
        images,
        key=lambda image: (
            not _is_front_view(image),
            not bool(image.is_primary),
            image.display_order,
            image.id,
        ),
    )


def _primary_image_url(images):
    ordered_images = _ordered_product_images(images)
    return ordered_images[0].image_url if ordered_images else None


def _deserialize_tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def build_products_count_cache_key(request: Request) -> str:
    filtered_params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in {"page", "limit", "sort_by"}
    ]
    filtered_params.sort()
    encoded = urlencode(filtered_params, doseq=True)
    return f"cache:products:count:{request.url.path}?{encoded}"


def _split_filter_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).split(",")
    return [str(entry).strip() for entry in raw_values if str(entry).strip()]


def _category_ids_with_descendants(db: Session, slugs: list[str]) -> list[int]:
    selected_ids = {
        category_id
        for (category_id,) in db.query(Category.id).filter(Category.slug.in_(slugs)).all()
    }
    all_ids = set(selected_ids)
    frontier = selected_ids
    while frontier:
        child_ids = {
            category_id
            for (category_id,) in db.query(Category.id).filter(Category.parent_id.in_(frontier)).all()
        } - all_ids
        all_ids.update(child_ids)
        frontier = child_ids
    return list(all_ids)


def _apply_search_filter(query, search: str, db: Session):
    search_value = str(search or "").strip()
    if not search_value:
        return query

    search_terms = [term.strip() for term in search_value.split() if term.strip()]
    if not search_terms:
        return query

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        vector = func.to_tsvector(
            "simple",
            func.concat(
                func.coalesce(Product.name, ""),
                " ",
                func.coalesce(Product.description, ""),
            ),
        )
        ts_query = func.websearch_to_tsquery("simple", " ".join(search_terms))
        return query.filter(vector.op("@@")(ts_query))

    for term in search_terms:
        search_term = f"%{term}%"
        query = query.filter(
            or_(
                Product.name.ilike(search_term),
                Product.description.ilike(search_term),
            )
        )
    return query


def build_products_query(
    db: Session,
    *,
    category_id: int | None = None,
    subcategory_id: int | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    occasion: str | None = None,
    fabric: str | None = None,
    color: str | None = None,
    size: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    search: str | None = None,
    featured: bool | None = None,
    is_featured: bool | None = None,
    in_stock_only: bool | None = None,
):
    query = (
        db.query(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.subcategory),
            selectinload(Product.images),
            selectinload(Product.variants),
            selectinload(Product.occasions),
        )
        .filter(Product.is_active == True)
    )

    if category_id:
        query = query.filter(Product.category_id == category_id)
    elif category:
        categories = _split_filter_values(category)
        if categories:
            cat_ids = _category_ids_with_descendants(db, categories)
            query = query.filter(Product.category_id.in_(cat_ids))

    if subcategory_id:
        query = query.filter(Product.subcategory_id == subcategory_id)
    elif subcategory:
        subcategories = _split_filter_values(subcategory)
        if subcategories:
            subcat_ids = [subcat.id for subcat in db.query(Subcategory).filter(Subcategory.slug.in_(subcategories)).all()]
            if subcat_ids:
                query = query.filter(Product.subcategory_id.in_(subcat_ids))

    if occasion:
        occasions = _split_filter_values(occasion)
        if occasions:
            query = query.join(Product.occasions).filter(Occasion.slug.in_(occasions))

    if fabric:
        fabrics = _split_filter_values(fabric)
        if fabrics:
            query = query.filter(func.lower(Product.fabric).in_([entry.lower() for entry in fabrics]))

    join_variants = False
    if color:
        colors = _split_filter_values(color)
        if colors:
            join_variants = True
            query = query.filter(func.lower(ProductVariant.color).in_([entry.lower() for entry in colors]))

    if size:
        sizes = _split_filter_values(size)
        if sizes:
            join_variants = True
            query = query.filter(func.lower(ProductVariant.size).in_([entry.lower() for entry in sizes]))

    if join_variants:
        query = query.join(Product.variants).filter(ProductVariant.is_active == True)

    if min_price is not None:
        query = query.filter(
            or_(
                Product.sale_price >= min_price,
                and_(Product.sale_price == None, Product.base_price >= min_price),
            )
        )

    if max_price is not None:
        query = query.filter(
            or_(
                Product.sale_price <= max_price,
                and_(Product.sale_price == None, Product.base_price <= max_price),
            )
        )

    if search:
        query = _apply_search_filter(query, search, db)

    featured_value = is_featured if is_featured is not None else featured
    if featured_value is not None:
        query = query.filter(Product.is_featured == featured_value)

    if in_stock_only:
        query = query.filter(Product.total_stock > 0)

    return query.distinct()


def apply_product_sort(query, sort_by: str | None):
    if sort_by == "price_asc":
        return query.order_by(Product.sale_price.asc().nullslast(), Product.base_price.asc())
    if sort_by == "price_desc":
        return query.order_by(Product.sale_price.desc().nullsfirst(), Product.base_price.desc())
    if sort_by == "newest":
        return query.order_by(Product.created_at.desc())
    if sort_by == "popular":
        return query.order_by(Product.review_count.desc(), Product.avg_rating.desc(), Product.id.desc())
    return query.order_by(Product.id.desc())


def get_cached_product_count(request: Request, query) -> int:
    count_cache_key = build_products_count_cache_key(request)
    total = cache_get_json(count_cache_key)
    if total is not None:
        return int(total)

    total = query.order_by(None).count()
    cache_set_json(count_cache_key, total, ttl_seconds=PRODUCT_COUNT_CACHE_TTL_SECONDS)
    return int(total)


def serialize_product_summary(product: Product) -> dict:
    primary_image = _primary_image_url(product.images)

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
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "base_price": product.base_price,
        "sale_price": product.sale_price,
        "discount_percentage": product.discount_percentage,
        "is_featured": product.is_featured,
        "is_bestseller": product.is_bestseller,
        "is_new_arrival": product.is_new_arrival,
        "collection": product.collection,
        "tags": _deserialize_tags(product.tags),
        "stock_quantity": stock_quantity,
        "default_variant": default_variant,
        "category": {
            "id": product.category.id,
            "name": product.category.name,
            "slug": product.category.slug,
        } if product.category else None,
        "subcategory": {
            "id": product.subcategory.id,
            "name": product.subcategory.name,
            "slug": product.subcategory.slug,
        } if product.subcategory else None,
        "primary_image": primary_image,
        "in_stock": stock_quantity > 0,
        "fabric": product.fabric,
        "colors": sorted({variant.color for variant in active_variants if variant.color}),
        "sizes": sorted({variant.size for variant in active_variants if variant.size}),
        "occasions": [occ.slug for occ in product.occasions],
    }


def list_products(
    db: Session,
    request: Request,
    *,
    page: int,
    limit: int,
    sort_by: str | None = None,
    **filters,
) -> dict:
    cache_key = f"cache:products:list:{request.url.path}?{request.url.query}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached

    query = build_products_query(db, **filters)
    total = get_cached_product_count(request, query)
    products = apply_product_sort(query, sort_by).offset((page - 1) * limit).limit(limit).all()

    response = {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit if limit else 0,
        "products": [serialize_product_summary(product) for product in products],
    }
    cache_set_json(cache_key, response, ttl_seconds=PRODUCT_LIST_CACHE_TTL_SECONDS)
    return response


def get_product_detail(db: Session, *, slug: str) -> dict:
    cache_key = f"cache:products:detail:{slug}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached

    product = (
        db.query(Product)
        .options(
            joinedload(Product.images),
            joinedload(Product.category),
            joinedload(Product.subcategory),
            selectinload(Product.variants),
            selectinload(Product.occasions),
        )
        .filter(Product.slug == slug, Product.is_active == True)
        .first()
    )
    if not product:
        raise ProductNotFound()

    primary_image = _primary_image_url(product.images)

    data = {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "base_price": product.base_price,
        "sale_price": product.sale_price,
        "discount_percentage": product.discount_percentage,
        "is_featured": product.is_featured,
        "is_bestseller": product.is_bestseller,
        "is_new_arrival": product.is_new_arrival,
        "collection": product.collection,
        "tags": _deserialize_tags(product.tags),
        "total_stock": product.total_stock,
        "avg_rating": product.avg_rating,
        "review_count": product.review_count,
        "fabric": product.fabric,
        "care_instructions": product.care_instructions,
        "category": {
            "id": product.category.id,
            "name": product.category.name,
            "slug": product.category.slug,
        } if product.category else None,
        "subcategory": {
            "id": product.subcategory.id,
            "name": product.subcategory.name,
            "slug": product.subcategory.slug,
        } if product.subcategory else None,
        "primary_image": primary_image,
        "in_stock": any(v.stock_quantity > 0 for v in product.variants),
        "images": [
            {
                "id": img.id,
                "image_url": img.image_url,
                "alt_text": img.alt_text,
                "display_order": img.display_order,
                "is_primary": img.is_primary,
            }
            for img in _ordered_product_images(product.images)
        ],
        "variants": [
            {
                "id": variant.id,
                "size": variant.size,
                "color": variant.color,
                "sku": variant.sku,
                "stock_quantity": variant.stock_quantity,
                "additional_price": variant.additional_price,
                "is_active": variant.is_active,
            }
            for variant in product.variants if variant.is_active
        ],
        "occasions": [
            {"id": occasion.id, "name": occasion.name, "slug": occasion.slug}
            for occasion in product.occasions
        ],
        "created_at": product.created_at,
    }
    cache_set_json(cache_key, data, ttl_seconds=PRODUCT_DETAIL_CACHE_TTL_SECONDS)
    return data


def get_product_delivery_estimate(db: Session, *, slug: str, pincode: str, free_shipping_threshold: float, default_shipping_charge: float) -> dict:
    product = db.query(Product).filter(Product.slug == slug, Product.is_active == True).first()
    if not product:
        raise ProductNotFound()

    current_price = product.sale_price if product.sale_price is not None else product.base_price
    shipping_cost = 0.0 if current_price >= free_shipping_threshold else default_shipping_charge

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
    return {
        "pincode": pincode,
        "cod_available": cod_available,
        "shipping_cost": shipping_cost,
        "delivery_days_min": min_days,
        "delivery_days_max": max_days,
        "estimated_delivery_date_start": (today + timedelta(days=min_days)).isoformat(),
        "estimated_delivery_date_end": (today + timedelta(days=max_days)).isoformat(),
    }

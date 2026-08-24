import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.category import Category, Subcategory
from app.models.product import Occasion, Product, ProductImage, ProductVariant
from app.schemas.catalog_import import CatalogImportRequest, CatalogProductImport


@dataclass
class CatalogImportValidationError(Exception):
    errors: list[dict]


def _error(index: int, code: str, message: str, *, slug: str | None = None, sku: str | None = None) -> dict:
    return {
        "product_index": index,
        "slug": slug,
        "sku": sku,
        "code": code,
        "message": message,
    }


def _validate_request(db: Session, payload: CatalogImportRequest) -> tuple[list[dict], dict]:
    errors: list[dict] = []
    slugs: dict[str, int] = {}
    skus: dict[str, int] = {}
    external_ids: dict[tuple[str, str], int] = {}

    category_slugs = {item.category_slug for item in payload.products}
    subcategory_slugs = {item.subcategory_slug for item in payload.products if item.subcategory_slug}
    occasion_slugs = {slug for item in payload.products for slug in item.occasion_slugs}

    categories = {
        item.slug: item
        for item in db.query(Category).filter(Category.slug.in_(category_slugs)).all()
    }
    subcategories = {
        item.slug: item
        for item in db.query(Subcategory).filter(Subcategory.slug.in_(subcategory_slugs)).all()
    } if subcategory_slugs else {}
    occasions = {
        item.slug: item
        for item in db.query(Occasion).filter(Occasion.slug.in_(occasion_slugs)).all()
    } if occasion_slugs else {}

    incoming_slugs = [item.slug for item in payload.products]
    existing_products = {
        item.slug: item
        for item in db.query(Product).filter(Product.slug.in_(incoming_slugs)).all()
    }
    incoming_skus = [variant.sku for item in payload.products for variant in item.variants]
    existing_variants = {
        item.sku: item
        for item in db.query(ProductVariant).filter(ProductVariant.sku.in_(incoming_skus)).all()
    }

    external_pairs = [
        (item.external_source, item.external_id)
        for item in payload.products
        if item.external_source and item.external_id
    ]
    external_products: dict[tuple[str, str], Product] = {}
    for source, external_id in external_pairs:
        product = (
            db.query(Product)
            .filter(Product.external_source == source, Product.external_id == external_id)
            .first()
        )
        if product:
            external_products[(source, external_id)] = product

    targets: dict[int, Product | None] = {}
    for index, item in enumerate(payload.products, start=1):
        if item.slug in slugs:
            errors.append(_error(index, "duplicate_slug", f"Slug also appears at product {slugs[item.slug]}", slug=item.slug))
        else:
            slugs[item.slug] = index

        pair = (item.external_source, item.external_id) if item.external_source and item.external_id else None
        if pair:
            if pair in external_ids:
                errors.append(_error(index, "duplicate_external_id", f"External identity also appears at product {external_ids[pair]}", slug=item.slug))
            else:
                external_ids[pair] = index

        category = categories.get(item.category_slug)
        if not category:
            errors.append(_error(index, "category_not_found", f"Unknown category_slug: {item.category_slug}", slug=item.slug))
        elif not category.is_active:
            errors.append(_error(index, "category_inactive", f"Category is inactive: {item.category_slug}", slug=item.slug))

        if item.subcategory_slug:
            subcategory = subcategories.get(item.subcategory_slug)
            if not subcategory:
                errors.append(_error(index, "subcategory_not_found", f"Unknown subcategory_slug: {item.subcategory_slug}", slug=item.slug))
            elif category and subcategory.category_id != category.id:
                errors.append(_error(index, "subcategory_mismatch", "Subcategory does not belong to category", slug=item.slug))

        for occasion_slug in item.occasion_slugs:
            if occasion_slug not in occasions:
                errors.append(_error(index, "occasion_not_found", f"Unknown occasion_slug: {occasion_slug}", slug=item.slug))

        slug_product = existing_products.get(item.slug)
        external_product = external_products.get(pair) if pair else None
        if slug_product and external_product and slug_product.id != external_product.id:
            errors.append(_error(index, "identity_conflict", "Slug and external identity resolve to different products", slug=item.slug))
        target = external_product or slug_product
        targets[index] = target
        if payload.mode == "create" and target:
            errors.append(_error(index, "product_exists", "Product already exists; use upsert mode", slug=item.slug))

        for variant in item.variants:
            if variant.sku in skus:
                errors.append(_error(index, "duplicate_sku", f"SKU also appears at product {skus[variant.sku]}", slug=item.slug, sku=variant.sku))
            else:
                skus[variant.sku] = index
            existing_variant = existing_variants.get(variant.sku)
            if existing_variant and (not target or existing_variant.product_id != target.id):
                errors.append(_error(index, "sku_conflict", "SKU already belongs to another product", slug=item.slug, sku=variant.sku))

    context = {
        "categories": categories,
        "subcategories": subcategories,
        "occasions": occasions,
        "targets": targets,
    }
    return errors, context


def _apply_product(
    db: Session,
    item: CatalogProductImport,
    target: Product | None,
    context: dict,
) -> tuple[Product, bool, int, int]:
    created = target is None
    product = target or Product()
    if created:
        db.add(product)

    category = context["categories"][item.category_slug]
    subcategory = context["subcategories"].get(item.subcategory_slug)
    discount = Decimal("0")
    if item.sale_price is not None and item.sale_price < item.base_price:
        discount = ((item.base_price - item.sale_price) / item.base_price) * 100

    product.name = item.name
    product.slug = item.slug
    product.category_id = category.id
    product.subcategory_id = subcategory.id if subcategory else None
    product.base_price = item.base_price
    product.sale_price = item.sale_price
    product.discount_percentage = int(discount)
    product.description = item.description
    product.fabric = item.fabric
    product.care_instructions = item.care_instructions
    product.meta_title = item.meta_title
    product.meta_description = item.meta_description
    product.audience = item.audience
    product.collection = item.collection
    product.tags = json.dumps(item.tags, separators=(",", ":"))
    product.catalog_status = item.status
    product.is_active = item.status == "active"
    product.is_featured = item.is_featured
    product.is_bestseller = item.is_bestseller
    product.is_new_arrival = item.is_new_arrival
    product.external_source = item.external_source
    product.external_id = item.external_id
    product.style_code = item.style_code
    product.occasions = [context["occasions"][slug] for slug in item.occasion_slugs]
    db.flush()

    if item.images:
        for image in list(product.images):
            db.delete(image)
        for index, image in enumerate(item.images):
            db.add(
                ProductImage(
                    product_id=product.id,
                    image_url=str(image.image_url),
                    alt_text=image.alt_text or item.name,
                    display_order=image.display_order,
                    is_primary=image.is_primary or (index == 0 and not any(value.is_primary for value in item.images)),
                )
            )

    variants_by_sku = {variant.sku: variant for variant in product.variants}
    supplied_skus = {variant.sku for variant in item.variants}
    created_variants = 0
    updated_variants = 0
    for variant_payload in item.variants:
        variant = variants_by_sku.get(variant_payload.sku)
        if variant is None:
            variant = ProductVariant(product_id=product.id, sku=variant_payload.sku)
            db.add(variant)
            created_variants += 1
        else:
            updated_variants += 1
        variant.size = variant_payload.size
        variant.color = variant_payload.color
        variant.stock_quantity = variant_payload.stock_quantity
        variant.additional_price = variant_payload.additional_price
        variant.is_active = variant_payload.is_active

    for sku, variant in variants_by_sku.items():
        if sku not in supplied_skus:
            variant.stock_quantity = 0
            variant.is_active = False

    return product, created, created_variants, updated_variants


def import_catalog(db: Session, payload: CatalogImportRequest) -> dict:
    errors, context = _validate_request(db, payload)
    if errors:
        raise CatalogImportValidationError(errors)

    report = {
        "dry_run": payload.dry_run,
        "mode": payload.mode,
        "products_received": len(payload.products),
        "products_created": 0,
        "products_updated": 0,
        "variants_created": 0,
        "variants_updated": 0,
        "errors": [],
    }
    if payload.dry_run:
        return report

    changed_slugs: list[str] = []
    try:
        for index, item in enumerate(payload.products, start=1):
            product, created, variants_created, variants_updated = _apply_product(
                db,
                item,
                context["targets"][index],
                context,
            )
            changed_slugs.append(product.slug)
            report["products_created" if created else "products_updated"] += 1
            report["variants_created"] += variants_created
            report["variants_updated"] += variants_updated
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise CatalogImportValidationError(
            [_error(0, "database_conflict", "Catalog changed during import; retry after refreshing the source file")]
        ) from exc
    except Exception:
        db.rollback()
        raise

    report["changed_slugs"] = changed_slugs
    return report


from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Dict, List

from app.core.rate_limiter import limiter
from app.db.session import get_db
from app.models.category import Category, Subcategory
from app.models.product import Product
from app.utils.response import success

router = APIRouter()


def _normalize(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "-")


def _classify_audience(category: Category) -> str:
    slug = _normalize(category.slug)
    name = _normalize(category.name)
    text = f"{slug} {name}"

    women_keywords = {
        "women", "woman", "ladies", "lehenga", "lehenga-choli", "saree", "sarees",
        "anarkali", "gown", "gowns", "salwar", "salwar-suits", "blouse", "blouses",
        "dupatta", "dupattas", "stole", "stoles", "kurta-sets-women", "kurti"
    }
    kids_keywords = {
        "kids", "kid", "boys", "girls", "boy", "girl", "children", "child",
        "kids-accessories", "boys-kurta", "girls-lehenga", "girls-gowns"
    }
    men_keywords = {
        "men", "mens", "sherwani", "kurta", "kurta-sets", "kurta-pajama", "indo-western",
        "bandhgala", "jodhpuri", "achkan", "nehru", "jacket", "waistcoat", "churidar",
        "patiala", "dhoti", "accessories"
    }

    if any(key in text for key in kids_keywords):
        return "kids"
    if any(key in text for key in women_keywords):
        return "women"
    if any(key in text for key in men_keywords):
        return "men"
    return "men"


@router.get("", response_model=dict)
@router.get("/", response_model=dict)
@limiter.limit("100/minute")
def get_public_categories(
    request: Request,
    include_children: bool = Query(False),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Public: Return active categories ordered for frontend navigation."""
    query = db.query(Category)
    if active_only:
        query = query.filter(Category.is_active == True)
    categories = query.order_by(Category.display_order.asc(), Category.id.asc()).all()

    if not include_children:
        return success(data=categories, message="Categories retrieved")

    subcat_query = db.query(Subcategory)
    if active_only:
        subcat_query = subcat_query.filter(Subcategory.is_active == True)
    subcategories = subcat_query.order_by(Subcategory.id.asc()).all()

    subcats_by_category: Dict[int, List[Subcategory]] = {}
    for subcat in subcategories:
        subcats_by_category.setdefault(subcat.category_id, []).append(subcat)

    subcat_counts = dict(
        db.query(Product.subcategory_id, func.count(Product.id))
        .filter(Product.subcategory_id.isnot(None))
        .group_by(Product.subcategory_id)
        .all()
    )

    l1_slugs = {"men", "women", "kids"}
    l1_nodes: Dict[str, dict] = {}

    def ensure_l1(slug: str, name: str) -> dict:
        if slug not in l1_nodes:
            l1_nodes[slug] = {
                "id": f"l1-{slug}",
                "name": name,
                "slug": slug,
                "level": 1,
                "parent_id": None,
                "display_order": 0,
                "is_active": True,
                "children": []
            }
        return l1_nodes[slug]

    # Bootstrap L1 from actual categories if present
    l1_by_category_id: Dict[int, dict] = {}
    for category in categories:
        if _normalize(category.slug) in l1_slugs:
            l1 = ensure_l1(_normalize(category.slug), category.name)
            l1["id"] = category.id
            l1["display_order"] = category.display_order
            l1_by_category_id[category.id] = l1

    # Create missing L1 shells if not present
    ensure_l1("men", "Men")
    ensure_l1("women", "Women")
    ensure_l1("kids", "Kids")

    l2_candidates = [cat for cat in categories if _normalize(cat.slug) not in l1_slugs]

    for category in l2_candidates:
        l1 = None
        if category.parent_id is not None:
            l1 = l1_by_category_id.get(category.parent_id)
        if l1 is None:
            audience = _classify_audience(category)
            l1 = ensure_l1(audience, audience.capitalize())
        l2_node = {
            "id": category.id,
            "name": category.name,
            "slug": category.slug,
            "level": 2,
            "parent_id": l1["id"],
            "display_order": category.display_order,
            "is_active": category.is_active,
            "children": []
        }

        children = []
        for subcat in subcats_by_category.get(category.id, []):
            children.append({
                "id": subcat.id,
                "name": subcat.name,
                "slug": subcat.slug,
                "level": 3,
                "parent_id": category.id,
                "display_order": 0,
                "is_active": subcat.is_active,
                "product_count": subcat_counts.get(subcat.id, 0)
            })

        l2_node["children"] = children
        l1["children"].append(l2_node)

    data = list(l1_nodes.values())
    for node in data:
        node_children = sorted(node.get("children", []), key=lambda child: (child.get("display_order", 0), child.get("id", 0)))
        node["children"] = node_children
        node["subcategories"] = [
            {"id": child["id"], "name": child["name"], "slug": child["slug"]}
            for child in node_children
        ]
    data.sort(key=lambda entry: entry.get("display_order", 0))
    return success(data=data, message="Categories retrieved")

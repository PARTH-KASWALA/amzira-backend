from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.product import Product, ProductImage, ProductVariant


def _create_product_with_images(db: Session) -> Product:
    category = Category(
        name="Men",
        slug="men",
        is_active=True,
    )
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name="Sherwani 01",
        slug="sherwani-01",
        description="Test product",
        base_price=1000.0,
        sale_price=None,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

    images = [
        ProductImage(
            product_id=product.id,
            image_url="/static/products/men/sherwani-01-back.jpg",
            alt_text="Back",
            display_order=2,
            is_primary=False,
        ),
        ProductImage(
            product_id=product.id,
            image_url="/static/products/men/sherwani-01-front.jpg",
            alt_text="Front",
            display_order=0,
            is_primary=True,
        ),
        ProductImage(
            product_id=product.id,
            image_url="/static/products/men/sherwani-01-side.jpg",
            alt_text="Side",
            display_order=1,
            is_primary=False,
        ),
    ]
    db.add_all(images)
    db.add(
        ProductVariant(
            product_id=product.id,
            size="M",
            color="Maroon",
            sku="AMZ-TEST-M-MAROON",
            stock_quantity=3,
            is_active=True,
        )
    )
    db.commit()
    db.refresh(product)
    return product


def test_product_detail_returns_multiple_images_ordered(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)

    response = client.get(f"/api/v1/products/{product.slug}")

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]

    images = data["images"]
    assert len(images) == 3

    assert images[0]["is_primary"] is True
    assert images[0]["display_order"] == 0

    display_orders = [img["display_order"] for img in images]
    assert display_orders == [0, 1, 2]

    assert images[0]["image_url"].endswith("sherwani-01-front.jpg")
    assert images[1]["image_url"].endswith("sherwani-01-side.jpg")
    assert images[2]["image_url"].endswith("sherwani-01-back.jpg")


def test_front_view_wins_when_primary_flag_points_to_back_view(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    for image in product.images:
        image.is_primary = image.alt_text == "Back"
    db_session.commit()

    response = client.get(f"/api/v1/products/{product.slug}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["primary_image"].endswith("sherwani-01-front.jpg")
    assert data["images"][0]["alt_text"] == "Front"


def test_product_detail_includes_rating_stock_and_variant_sku(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    product.avg_rating = 4.7
    product.review_count = 12
    db_session.commit()

    response = client.get(f"/api/v1/products/{product.slug}")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["avg_rating"] == 4.7
    assert data["review_count"] == 12
    assert data["total_stock"] == 3
    assert data["variants"][0]["sku"] == "AMZ-TEST-M-MAROON"


def test_delivery_estimate_endpoint_returns_shipping_and_dates(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    product.base_price = 2600.0
    product.sale_price = None
    db_session.commit()

    response = client.get(f"/api/v1/products/{product.slug}/delivery-estimate?pincode=400001")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["pincode"] == "400001"
    assert data["cod_available"] is True
    assert data["shipping_cost"] == 0.0
    assert data["delivery_days_min"] == 2
    assert data["delivery_days_max"] == 4
    assert isinstance(data["estimated_delivery_date_start"], str)
    assert isinstance(data["estimated_delivery_date_end"], str)


def test_health_response_includes_hardened_csp_headers(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    csp = response.headers.get("content-security-policy", "")
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_delivery_estimate_endpoint_rejects_invalid_pincode(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    response = client.get(f"/api/v1/products/{product.slug}/delivery-estimate?pincode=123")

    assert response.status_code == 400
    assert response.json()["message"] == "Pincode must be 6 digits"


def test_product_search_requires_all_terms_to_match(client: TestClient, db_session: Session):
    category = Category(
        name="Women",
        slug="women",
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()

    matching_product = Product(
        category_id=category.id,
        name="Rose Gold Lehenga",
        slug="rose-gold-lehenga",
        description="Premium festive lehenga for wedding events",
        base_price=5000.0,
        is_active=True,
    )
    non_matching_product = Product(
        category_id=category.id,
        name="Rose Kurta",
        slug="rose-kurta",
        description="Casual kurta set",
        base_price=2500.0,
        is_active=True,
    )
    db_session.add_all([matching_product, non_matching_product])
    db_session.commit()

    response = client.get("/api/v1/products?search=rose lehenga")

    assert response.status_code == 200
    products = response.json()["data"]["products"]
    assert len(products) == 1
    assert products[0]["slug"] == "rose-gold-lehenga"


def test_product_list_supports_server_side_variant_and_fabric_filters(client: TestClient, db_session: Session):
    category = Category(
        name="Women",
        slug="women",
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()

    matching_product = Product(
        category_id=category.id,
        name="Red Silk Kurti",
        slug="red-silk-kurti",
        description="Silk kurti",
        base_price=3200.0,
        sale_price=2800.0,
        fabric="Silk",
        is_active=True,
    )
    other_product = Product(
        category_id=category.id,
        name="Blue Cotton Kurti",
        slug="blue-cotton-kurti",
        description="Cotton kurti",
        base_price=2200.0,
        sale_price=1900.0,
        fabric="Cotton",
        is_active=True,
    )
    db_session.add_all([matching_product, other_product])
    db_session.flush()

    db_session.add_all(
        [
            ProductImage(product_id=matching_product.id, image_url="/static/red.jpg", alt_text="Red", display_order=0, is_primary=True),
            ProductImage(product_id=other_product.id, image_url="/static/blue.jpg", alt_text="Blue", display_order=0, is_primary=True),
            ProductVariant(product_id=matching_product.id, size="M", color="Red", sku="RSK-M-RED", stock_quantity=3, is_active=True),
            ProductVariant(product_id=other_product.id, size="L", color="Blue", sku="BCK-L-BLUE", stock_quantity=4, is_active=True),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/products?category=women&fabric=Silk&color=Red&size=M")

    assert response.status_code == 200
    products = response.json()["data"]["products"]
    assert len(products) == 1
    assert products[0]["slug"] == "red-silk-kurti"


def test_product_list_parent_category_includes_child_products(client: TestClient, db_session: Session):
    parent = Category(name="Kids", slug="kids", is_active=True)
    db_session.add(parent)
    db_session.flush()
    child = Category(
        name="Girls Lehenga Choli",
        slug="girls-lehenga-choli",
        parent_id=parent.id,
        is_active=True,
    )
    unrelated = Category(name="Women", slug="women", is_active=True)
    db_session.add_all([child, unrelated])
    db_session.flush()
    db_session.add_all(
        [
            Product(
                category_id=child.id,
                name="Kids Pattu Pavadai",
                slug="kids-pattu-pavadai",
                base_price=1500.0,
                is_active=True,
            ),
            Product(
                category_id=unrelated.id,
                name="Women Kurta",
                slug="women-kurta",
                base_price=1800.0,
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/products?category=kids")

    assert response.status_code == 200
    products = response.json()["data"]["products"]
    assert [product["slug"] for product in products] == ["kids-pattu-pavadai"]

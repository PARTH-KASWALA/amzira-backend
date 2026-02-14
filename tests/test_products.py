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
        total_stock=0,
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


def test_product_detail_includes_rating_stock_and_variant_sku(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    product.avg_rating = 4.7
    product.review_count = 12
    product.total_stock = 3
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


def test_delivery_estimate_endpoint_rejects_invalid_pincode(client: TestClient, db_session: Session):
    product = _create_product_with_images(db_session)
    response = client.get(f"/api/v1/products/{product.slug}/delivery-estimate?pincode=123")

    assert response.status_code == 400
    assert response.json()["message"] == "Pincode must be 6 digits"

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.security import hash_password
from app.models.address import Address
from app.models.category import Category
from app.models.product import Occasion, Product, ProductImage, ProductVariant
from app.models.user import User


def _create_user(db: Session, email: str, phone: str) -> User:
    user = User(
        email=email,
        full_name="Launch Smoke User",
        phone=phone,
        password_hash=hash_password("StrongPass1"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def _create_address(db: Session, user_id: int) -> Address:
    address = Address(
        user_id=user_id,
        full_name="Launch User",
        phone="9876543210",
        address_line1="Street 1",
        city="Surat",
        state="Gujarat",
        pincode="395007",
        country="India",
        address_type="home",
        is_default=True,
    )
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


def _create_product_bundle(
    db: Session,
    *,
    suffix: str,
    category_slug: str,
    stock: int = 5,
    occasion_slug: str | None = None,
) -> ProductVariant:
    category = db.query(Category).filter(Category.slug == category_slug).first()
    if not category:
        category = Category(name=f"Category {category_slug}", slug=category_slug, is_active=True)
        db.add(category)
        db.flush()

    product = Product(
        category_id=category.id,
        name=f"Product {suffix}",
        slug=f"product-{suffix}",
        base_price=1200.0,
        sale_price=999.0,
        total_stock=stock,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

    if occasion_slug:
        occasion = db.query(Occasion).filter(Occasion.slug == occasion_slug).first()
        if not occasion:
            occasion = Occasion(name=occasion_slug.capitalize(), slug=occasion_slug)
            db.add(occasion)
            db.flush()
        product.occasions.append(occasion)

    db.add(
        ProductImage(
            product_id=product.id,
            image_url=f"https://cdn.amzira.test/{suffix}.jpg",
            alt_text=product.name,
            display_order=0,
            is_primary=True,
        )
    )

    variant = ProductVariant(
        product_id=product.id,
        size="L",
        color="Green",
        sku=f"SKU-{suffix.upper()}",
        stock_quantity=stock,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def test_cart_add_requires_variant_id_with_clear_400(client: TestClient, db_session: Session):
    user = _create_user(db_session, "cart-variant@example.com", "9876543261")
    _login(client, user.email)

    response = client.post(
        "/api/v1/cart/items",
        headers=_csrf_headers(client),
        json={"product_id": 1, "quantity": 1},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["message"] == "variant_id is required and must be selected"
    assert payload["data"] is None


def test_product_list_by_category_slug_returns_standard_fields(client: TestClient, db_session: Session):
    men_variant = _create_product_bundle(db_session, suffix="men", category_slug="men", stock=7)
    _create_product_bundle(db_session, suffix="women", category_slug="women", stock=3)

    response = client.get("/api/v1/products?category=men")
    assert response.status_code == 200

    payload = response.json()
    products = payload["data"]["products"]
    assert len(products) == 1

    product = products[0]
    assert product["id"] == men_variant.product_id
    assert product["name"]
    assert product["slug"]
    assert product["base_price"] == 1200.0
    assert product["sale_price"] == 999.0
    assert product["primary_image"] == "https://cdn.amzira.test/men.jpg"
    assert product["stock_quantity"] == 7
    assert product["default_variant"]["variant_id"] == men_variant.id
    assert product["default_variant"]["stock_quantity"] == 7
    assert product["category"]["name"] == "Category men"
    assert product["category"]["slug"] == "men"


def test_product_list_sets_default_variant_null_when_out_of_stock(client: TestClient, db_session: Session):
    _create_product_bundle(db_session, suffix="soldout", category_slug="soldout", stock=0)

    response = client.get("/api/v1/products?category=soldout")
    assert response.status_code == 200

    payload = response.json()
    products = payload["data"]["products"]
    assert len(products) == 1
    assert products[0]["stock_quantity"] == 0
    assert products[0]["default_variant"] is None


def test_login_to_cod_checkout_smoke_flow(client: TestClient, db_session: Session):
    user = _create_user(db_session, "launch-smoke@example.com", "9876543262")
    variant = _create_product_bundle(db_session, suffix="checkout", category_slug="checkout", stock=4)
    address = _create_address(db_session, user.id)

    _login(client, user.email)
    headers = _csrf_headers(client)

    add_response = client.post(
        "/api/v1/cart/items",
        headers=headers,
        json={"product_id": variant.product_id, "variant_id": variant.id, "quantity": 1},
    )
    assert add_response.status_code == 201

    create_order_response = client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "cod",
            "idempotency_key": str(uuid4()),
        },
    )
    assert create_order_response.status_code == 201
    payload = create_order_response.json()
    assert payload["success"] is True
    assert payload["data"]["payment_method"] == "cod"


def test_catalog_launch_health_reports_soft_launch_gaps(client: TestClient, db_session: Session):
    _create_product_bundle(db_session, suffix="men-wedding", category_slug="men", occasion_slug="wedding")
    _create_product_bundle(db_session, suffix="women-wedding", category_slug="women", occasion_slug="wedding")
    _create_product_bundle(db_session, suffix="kids-festive", category_slug="kids", occasion_slug="festive")

    response = client.get("/health/catalog-launch")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "unhealthy"
    assert payload["requirements_total"] == 6
    assert payload["requirements_ready"] == 3
    assert payload["requirements_missing"] == 3

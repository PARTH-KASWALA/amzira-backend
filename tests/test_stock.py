from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.cart import CartItem
from app.models.category import Category
from app.models.product import Product, ProductVariant
from app.models.user import User


def _create_variant(db: Session, *, slug_suffix: str, stock: int) -> ProductVariant:
    category = Category(name=f"Stock Category {slug_suffix}", slug=f"stock-category-{slug_suffix}", is_active=True)
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name=f"Stock Product {slug_suffix}",
        slug=f"stock-product-{slug_suffix}",
        base_price=999.0,
        sale_price=None,
        total_stock=stock,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Blue",
        sku=f"STOCK-{slug_suffix.upper()}",
        stock_quantity=stock,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def _create_user(db: Session, email: str, phone: str) -> User:
    user = User(
        email=email,
        full_name="Stock User",
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


def test_stock_check_post_happy_and_insufficient(client: TestClient, db_session: Session):
    variant = _create_variant(db_session, slug_suffix="post", stock=2)

    headers = _csrf_headers(client)
    ok_response = client.post(
        "/api/v1/stock/check",
        headers=headers,
        json={"items": [{"variant_id": variant.id, "quantity": 1}]},
    )
    assert ok_response.status_code == 200
    ok_payload = ok_response.json()
    assert ok_payload["success"] is True
    assert ok_payload["data"]["available"] is True
    assert ok_payload["data"]["items"] == []

    insufficient_response = client.post(
        "/api/v1/stock/check",
        headers=headers,
        json={"items": [{"variant_id": variant.id, "quantity": 3}]},
    )
    assert insufficient_response.status_code == 200
    insufficient_payload = insufficient_response.json()
    assert insufficient_payload["data"]["available"] is False
    assert len(insufficient_payload["data"]["items"]) == 1
    assert insufficient_payload["data"]["items"][0]["variant_id"] == variant.id
    assert insufficient_payload["data"]["items"][0]["available_quantity"] == 2


def test_stock_check_legacy_get_uses_authenticated_cart_when_no_payload(client: TestClient, db_session: Session):
    user = _create_user(db_session, "stock-legacy@example.com", "9876543271")
    variant = _create_variant(db_session, slug_suffix="legacy", stock=4)
    db_session.add(
        CartItem(
            user_id=user.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            quantity=2,
            price_at_addition=999.0,
        )
    )
    db_session.commit()

    _login(client, user.email)
    response = client.get("/api/v1/stock/check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["available"] is True
    assert payload["data"]["items"] == []

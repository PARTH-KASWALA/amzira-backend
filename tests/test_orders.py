from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.security import hash_password
from app.models.address import Address
from app.models.cart import CartItem
from app.models.category import Category
from app.models.product import Product, ProductVariant
from app.models.user import User


def _create_user(db: Session, email: str, phone: str) -> User:
    user = User(
        email=email,
        full_name="Order Test User",
        phone=phone,
        password_hash=hash_password("StrongPass1"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_address(db: Session, user_id: int) -> Address:
    address = Address(
        user_id=user_id,
        full_name="Order User",
        phone="9876543210",
        address_line1="Line 1",
        city="Surat",
        state="Gujarat",
        pincode="395007",
        country="India",
        is_default=True,
        address_type="home",
    )
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


def _create_product_variant(db: Session, stock_quantity: int) -> ProductVariant:
    category = Category(
        name=f"Category-{stock_quantity}",
        slug=f"category-{stock_quantity}",
        is_active=True,
    )
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name=f"Product-{stock_quantity}",
        slug=f"product-{stock_quantity}",
        base_price=1000.0,
        sale_price=None,
        total_stock=stock_quantity,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Red",
        sku=f"SKU-{stock_quantity}-{product.id}",
        stock_quantity=stock_quantity,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def test_empty_cart_order_rejection(client: TestClient, db_session: Session):
    user = _create_user(db_session, "emptycart@example.com", "9876543213")
    address = _create_address(db_session, user.id)
    _login(client, user.email)

    response = client.post(
        "/api/v1/orders/",
        headers=_csrf_headers(client),
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["message"] == "Cart is empty"


def test_insufficient_stock(client: TestClient, db_session: Session):
    user = _create_user(db_session, "stock@example.com", "9876543214")
    address = _create_address(db_session, user.id)
    variant = _create_product_variant(db_session, stock_quantity=1)

    cart_item = CartItem(
        user_id=user.id,
        product_id=variant.product_id,
        variant_id=variant.id,
        quantity=2,
        price_at_addition=1000.0,
    )
    db_session.add(cart_item)
    db_session.commit()

    _login(client, user.email)
    response = client.post(
        "/api/v1/orders/",
        headers=_csrf_headers(client),
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert "Insufficient stock" in payload["message"]


def test_order_number_uniqueness(client: TestClient, db_session: Session):
    user = _create_user(db_session, "unique@example.com", "9876543215")
    address = _create_address(db_session, user.id)
    variant = _create_product_variant(db_session, stock_quantity=5)

    _login(client, user.email)
    headers = _csrf_headers(client)

    first_cart_item = CartItem(
        user_id=user.id,
        product_id=variant.product_id,
        variant_id=variant.id,
        quantity=1,
        price_at_addition=1000.0,
    )
    db_session.add(first_cart_item)
    db_session.commit()

    first_response = client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": str(uuid4()),
        },
    )
    assert first_response.status_code == 201
    first_order_number = first_response.json()["data"]["order_number"]

    second_cart_item = CartItem(
        user_id=user.id,
        product_id=variant.product_id,
        variant_id=variant.id,
        quantity=1,
        price_at_addition=1000.0,
    )
    db_session.add(second_cart_item)
    db_session.commit()

    second_response = client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": str(uuid4()),
        },
    )
    assert second_response.status_code == 201
    second_order_number = second_response.json()["data"]["order_number"]

    assert first_order_number != second_order_number


def test_order_creation_is_idempotent_with_same_key(client: TestClient, db_session: Session):
    user = _create_user(db_session, "idem@example.com", "9876543216")
    address = _create_address(db_session, user.id)
    variant = _create_product_variant(db_session, stock_quantity=3)
    idem_key = str(uuid4())

    _login(client, user.email)
    headers = _csrf_headers(client)

    db_session.add(
        CartItem(
            user_id=user.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            quantity=1,
            price_at_addition=1000.0,
        )
    )
    db_session.commit()

    first_response = client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": idem_key,
        },
    )
    assert first_response.status_code == 201
    first_order_number = first_response.json()["data"]["order_number"]

    # Re-submit with same key; should return existing order and avoid duplicate.
    second_response = client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "shipping_address_id": address.id,
            "billing_address_id": address.id,
            "payment_method": "razorpay",
            "idempotency_key": idem_key,
        },
    )
    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["message"] == "Order already exists"
    assert payload["data"]["order_number"] == first_order_number

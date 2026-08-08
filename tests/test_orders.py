from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.security import hash_password
from app.models.address import Address
from app.models.cart import CartItem
from app.models.category import Category
from app.models.order import Order, OrderStatus
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


def test_direct_order_creation_endpoint_is_removed_for_empty_cart(client: TestClient, db_session: Session):
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

    assert response.status_code == 405


def test_direct_order_creation_endpoint_is_removed_for_stock_validation(client: TestClient, db_session: Session):
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

    assert response.status_code == 405


def test_direct_order_creation_endpoint_is_removed_for_unique_order_number_flow(client: TestClient, db_session: Session):
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
    assert first_response.status_code == 405

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
    assert second_response.status_code == 405


def test_direct_order_creation_endpoint_is_removed_for_idempotency_flow(client: TestClient, db_session: Session):
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
    assert first_response.status_code == 405

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
    assert second_response.status_code == 405


def test_get_orders_returns_empty_list(client: TestClient, db_session: Session):
    user = _create_user(db_session, "orders-empty@example.com", "9876543218")

    _login(client, user.email)
    response = client.get("/api/v1/orders/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Orders retrieved"
    assert payload["data"] == []


def test_get_orders_returns_created_orders(client: TestClient, db_session: Session):
    user = _create_user(db_session, "orders-list@example.com", "9876543219")
    address = _create_address(db_session, user.id)
    order = Order(
        user_id=user.id,
        order_number="AMZ-LIST-0001",
        subtotal=1000.0,
        tax_amount=0.0,
        shipping_charge=0.0,
        discount_amount=0.0,
        total_amount=1000.0,
        status=OrderStatus.CONFIRMED,
        shipping_address_id=address.id,
        billing_address_id=address.id,
        idempotency_key=str(uuid4()),
        stock_deducted=True,
    )
    db_session.add(order)
    db_session.commit()

    _login(client, user.email)
    response = client.get("/api/v1/orders/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Orders retrieved"
    assert len(payload["data"]) == 1
    assert payload["data"][0]["order_number"] == order.order_number


def test_order_detail_requires_authenticated_owner(client: TestClient, db_session: Session):
    owner = _create_user(db_session, "detail-owner@example.com", "9876543291")
    other_user = _create_user(db_session, "detail-other@example.com", "9876543292")
    address = _create_address(db_session, owner.id)

    order = Order(
        user_id=owner.id,
        order_number="AMZDETAILSECURE1",
        subtotal=1500.0,
        tax_amount=0.0,
        shipping_charge=0.0,
        discount_amount=0.0,
        total_amount=1500.0,
        status=OrderStatus.CONFIRMED,
        shipping_address_id=address.id,
        billing_address_id=address.id,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)

    anonymous_response = client.get(f"/api/v1/orders/{order.order_number}")
    assert anonymous_response.status_code == 401

    _login(client, other_user.email)
    response = client.get(f"/api/v1/orders/{order.order_number}")
    assert response.status_code == 404

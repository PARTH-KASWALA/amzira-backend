import hashlib
import hmac
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.address import Address
from app.models.cart import CartItem
from app.models.category import Category
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import Product, ProductVariant
from app.models.user import User, UserRole
from app.services.order_service import auto_cancel_pending_orders
from app.api.deps import get_real_client_ip


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def _create_user(db: Session, email: str, phone: str, role: UserRole = UserRole.CUSTOMER) -> User:
    user = User(
        email=email,
        full_name="Critical Test User",
        phone=phone,
        password_hash=hash_password("StrongPass1"),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


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


def _create_variant(db: Session, stock: int, suffix: str) -> ProductVariant:
    category = Category(name=f"Cat-{suffix}", slug=f"cat-{suffix}", is_active=True)
    db.add(category)
    db.flush()
    product = Product(
        category_id=category.id,
        name=f"Product-{suffix}",
        slug=f"product-{suffix}",
        base_price=500.0,
        is_active=True,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Blue",
        sku=f"SKU-{suffix}",
        stock_quantity=stock,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _create_pending_order(db: Session, user_id: int, variant: ProductVariant, *, stock_deducted: bool, quantity: int = 1) -> Order:
    order = Order(
        order_number=f"AMZTEST{user_id}{variant.id}{int(datetime.utcnow().timestamp())}",
        user_id=user_id,
        subtotal=500.0,
        tax_amount=90.0,
        shipping_charge=100.0,
        total_amount=690.0,
        status=OrderStatus.PENDING,
        shipping_address_id=_create_address(db, user_id).id,
        billing_address_id=_create_address(db, user_id).id,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
        stock_deducted=stock_deducted,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            product_name="Product",
            variant_details="Size: M, Color: Blue",
            quantity=quantity,
            unit_price=500.0,
            total_price=500.0,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def test_concurrent_stock_purchase_prevents_oversell(client: TestClient, db_session: Session):
    user_one = _create_user(db_session, "race1@example.com", "9876543201")
    user_two = _create_user(db_session, "race2@example.com", "9876543202")
    variant = _create_variant(db_session, stock=1, suffix="race")

    order1 = _create_pending_order(db_session, user_one.id, variant, stock_deducted=False, quantity=1)
    order2 = _create_pending_order(db_session, user_two.id, variant, stock_deducted=False, quantity=1)

    payment1 = Payment(
        order_id=order1.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order1.total_amount,
        currency="INR",
        razorpay_order_id="razorpay_order_race_1",
    )
    payment2 = Payment(
        order_id=order2.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order2.total_amount,
        currency="INR",
        razorpay_order_id="razorpay_order_race_2",
    )
    db_session.add_all([payment1, payment2])
    db_session.commit()

    _login(client, user_one.email)
    valid_signature_1 = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        b"razorpay_order_race_1|payment_race_1",
        hashlib.sha256,
    ).hexdigest()
    success_response = client.post(
        "/api/v1/payments/verify",
        headers=_csrf_headers(client),
        json={
            "razorpay_order_id": "razorpay_order_race_1",
            "razorpay_payment_id": "payment_race_1",
            "razorpay_signature": valid_signature_1,
        },
    )
    assert success_response.status_code == 200

    _login(client, user_two.email)
    valid_signature_2 = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        b"razorpay_order_race_2|payment_race_2",
        hashlib.sha256,
    ).hexdigest()
    failed_response = client.post(
        "/api/v1/payments/verify",
        headers=_csrf_headers(client),
        json={
            "razorpay_order_id": "razorpay_order_race_2",
            "razorpay_payment_id": "payment_race_2",
            "razorpay_signature": valid_signature_2,
        },
    )
    assert failed_response.status_code == 400
    assert "Insufficient stock" in failed_response.json()["message"]


def test_payment_abandonment_cleanup_restores_stock(db_session: Session):
    user = _create_user(db_session, "abandoned@example.com", "9876543203")
    variant = _create_variant(db_session, stock=0, suffix="abandoned")
    order = _create_pending_order(db_session, user.id, variant, stock_deducted=True, quantity=1)

    cleaned = auto_cancel_pending_orders(db_session)
    db_session.refresh(order)
    db_session.refresh(variant)

    assert cleaned == 1
    assert order.status == OrderStatus.CANCELLED
    assert order.stock_deducted is False
    assert variant.stock_quantity == 1


def test_double_payment_prevention(client: TestClient, db_session: Session):
    user = _create_user(db_session, "doublepay@example.com", "9876543204")
    variant = _create_variant(db_session, stock=2, suffix="doublepay")
    order = _create_pending_order(db_session, user.id, variant, stock_deducted=False, quantity=1)
    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id="razorpay_double_order",
    )
    db_session.add(payment)
    db_session.commit()

    _login(client, user.email)
    signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        b"razorpay_double_order|payment_double_1",
        hashlib.sha256,
    ).hexdigest()

    first = client.post(
        "/api/v1/payments/verify",
        headers=_csrf_headers(client),
        json={
            "razorpay_order_id": "razorpay_double_order",
            "razorpay_payment_id": "payment_double_1",
            "razorpay_signature": signature,
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/payments/verify",
        headers=_csrf_headers(client),
        json={
            "razorpay_order_id": "razorpay_double_order",
            "razorpay_payment_id": "payment_double_1",
            "razorpay_signature": signature,
        },
    )
    assert second.status_code == 409


def test_cancel_pending_order_restores_stock(client: TestClient, db_session: Session):
    user = _create_user(db_session, "cancelpending@example.com", "9876543205")
    variant = _create_variant(db_session, stock=0, suffix="cancel")
    order = _create_pending_order(db_session, user.id, variant, stock_deducted=True, quantity=1)

    _login(client, user.email)
    response = client.put(
        f"/api/v1/orders/{order.id}/cancel",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200
    db_session.refresh(order)
    db_session.refresh(variant)
    assert order.status == OrderStatus.CANCELLED
    assert order.stock_deducted is False
    assert variant.stock_quantity == 1


def test_admin_ip_whitelist_enforcement(client: TestClient, db_session: Session):
    admin = _create_user(db_session, "adminip@example.com", "9876543206", role=UserRole.ADMIN)
    _login(client, admin.email)

    old_env = settings.ENVIRONMENT
    old_ips = settings.ADMIN_ALLOWED_IPS
    settings.ENVIRONMENT = "production"
    settings.ADMIN_ALLOWED_IPS = "10.10.10.10"
    try:
        response = client.post("/api/v1/admin/maintenance/cleanup-expired-orders", headers=_csrf_headers(client))
        assert response.status_code == 403
    finally:
        settings.ENVIRONMENT = old_env
        settings.ADMIN_ALLOWED_IPS = old_ips


def _build_request(client_ip: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/admin",
        "raw_path": b"/api/v1/admin",
        "query_string": b"",
        "headers": raw_headers,
        "client": (client_ip, 443),
        "server": ("testserver", 443),
    }
    return Request(scope)


def test_get_real_client_ip_uses_x_forwarded_for_from_trusted_proxy():
    old_env = settings.ENVIRONMENT
    old_trust_proxy_headers = settings.TRUST_PROXY_HEADERS
    old_trusted_proxy_ips = settings.TRUSTED_PROXY_IPS
    settings.ENVIRONMENT = "production"
    settings.TRUST_PROXY_HEADERS = True
    settings.TRUSTED_PROXY_IPS = "127.0.0.1"
    try:
        request = _build_request(
            "127.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.10, 127.0.0.1"},
        )
        client_ip, chain = get_real_client_ip(request)
        assert client_ip == "203.0.113.10"
        assert chain == ["203.0.113.10", "127.0.0.1"]
    finally:
        settings.ENVIRONMENT = old_env
        settings.TRUST_PROXY_HEADERS = old_trust_proxy_headers
        settings.TRUSTED_PROXY_IPS = old_trusted_proxy_ips


def test_get_real_client_ip_ignores_x_forwarded_for_from_untrusted_source():
    old_env = settings.ENVIRONMENT
    old_trust_proxy_headers = settings.TRUST_PROXY_HEADERS
    old_trusted_proxy_ips = settings.TRUSTED_PROXY_IPS
    settings.ENVIRONMENT = "production"
    settings.TRUST_PROXY_HEADERS = True
    settings.TRUSTED_PROXY_IPS = "127.0.0.1"
    try:
        request = _build_request(
            "198.51.100.20",
            headers={"X-Forwarded-For": "203.0.113.20"},
        )
        client_ip, chain = get_real_client_ip(request)
        assert client_ip == "198.51.100.20"
        assert chain == []
    finally:
        settings.ENVIRONMENT = old_env
        settings.TRUST_PROXY_HEADERS = old_trust_proxy_headers
        settings.TRUSTED_PROXY_IPS = old_trusted_proxy_ips


def test_razorpay_signature_tampering_rejection(client: TestClient, db_session: Session):
    user = _create_user(db_session, "tamper@example.com", "9876543207")
    variant = _create_variant(db_session, stock=1, suffix="tamper")
    order = _create_pending_order(db_session, user.id, variant, stock_deducted=False, quantity=1)
    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id="razorpay_tamper_order",
    )
    db_session.add(payment)
    db_session.commit()

    _login(client, user.email)
    response = client.post(
        "/api/v1/payments/verify",
        headers=_csrf_headers(client),
        json={
            "razorpay_order_id": "razorpay_tamper_order",
            "razorpay_payment_id": "payment_tamper_1",
            "razorpay_signature": "invalid_signature",
        },
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Invalid payment signature"


def test_webhook_retry_does_not_double_deduct_reserved_stock(client: TestClient, db_session: Session):
    user = _create_user(db_session, "webhookretry@example.com", "9876543208")
    variant = _create_variant(db_session, stock=1, suffix="webhook")
    order = _create_pending_order(db_session, user.id, variant, stock_deducted=True, quantity=1)
    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id="razorpay_webhook_order",
    )
    db_session.add(payment)
    db_session.commit()

    # Simulate that stock was already reserved at order creation.
    variant.stock_quantity = 0
    db_session.commit()

    event = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_webhook_1",
                    "order_id": "razorpay_webhook_order",
                    "amount": int(order.total_amount * 100),
                }
            }
        },
    }
    payload = json.dumps(event)
    signature = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = _csrf_headers(client)
    headers["X-Razorpay-Signature"] = signature
    headers["Content-Type"] = "application/json"

    first = client.post("/api/v1/payments/webhook", headers=headers, content=payload)
    assert first.status_code == 200
    db_session.refresh(payment)
    db_session.refresh(order)
    db_session.refresh(variant)
    assert payment.payment_status == PaymentStatus.SUCCESS
    assert order.status == OrderStatus.CONFIRMED
    assert variant.stock_quantity == 0

    second = client.post("/api/v1/payments/webhook", headers=headers, content=payload)
    assert second.status_code == 200
    assert second.json()["message"] == "Payment already processed"
    db_session.refresh(variant)
    assert variant.stock_quantity == 0

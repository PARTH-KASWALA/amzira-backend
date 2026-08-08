import hmac
import hashlib
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1 import commerce_checkout
from app.core.config import settings
from app.core.security import hash_password
from app.models.address import Address
from app.models.checkout_payment_intent import CheckoutPaymentIntent, CheckoutPaymentIntentStatus
from app.models.category import Category
from app.models.order import Order
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product, ProductImage, ProductVariant
from app.models.user import User


class _FakeRazorpayOrderClient:
    def create(self, payload: dict) -> dict:
        return {
            "id": "order_test_checkout_123",
            "amount": payload["amount"],
            "currency": payload["currency"],
        }


class _FakeRazorpayClient:
    def __init__(self):
        self.order = _FakeRazorpayOrderClient()


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token
    return {"X-CSRF-Token": token}


def _create_user(db: Session, email: str, phone: str) -> User:
    user = User(
        email=email,
        full_name="Commerce Checkout User",
        phone=phone,
        password_hash=hash_password("StrongPass1"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def _add_cart_item(
    client: TestClient,
    headers: dict,
    variant: ProductVariant,
    quantity: int,
) -> dict:
    response = client.post(
        "/api/v1/cart/items",
        headers=headers,
        json={
            "product_id": variant.product_id,
            "variant_id": variant.id,
            "quantity": quantity,
        },
    )
    assert response.status_code in {200, 201}
    return response.json()


def _get_cart(client: TestClient) -> dict:
    response = client.get("/api/v1/cart/")
    assert response.status_code == 200
    return response.json()["data"]


def _create_address_via_api(
    client: TestClient,
    headers: dict,
    *,
    full_name: str,
    phone: str,
    address_line1: str,
    city: str,
    state: str,
    pincode: str,
    is_default: bool = True,
) -> dict:
    response = client.post(
        "/api/v1/users/me/addresses",
        headers=headers,
        json={
            "full_name": full_name,
            "phone": phone,
            "address_line1": address_line1,
            "city": city,
            "state": state,
            "pincode": pincode,
            "country": "India",
            "address_type": "home",
            "is_default": is_default,
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


def _create_product_bundle(db: Session, suffix: str, stock: int = 5) -> ProductVariant:
    category = Category(name=f"Category {suffix}", slug=f"category-{suffix}", is_active=True)
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name=f"Product {suffix}",
        slug=f"product-{suffix}",
        base_price=1500.0,
        sale_price=1200.0,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

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
        size="M",
        color="Blue",
        sku=f"SKU-{suffix.upper()}",
        stock_quantity=stock,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def test_payment_verification_creates_order_and_clears_cart(
    client: TestClient,
    db_session: Session,
    monkeypatch,
):
    user = _create_user(db_session, "root-checkout@example.com", "9876543311")
    variant = _create_product_bundle(db_session, "root-checkout", stock=4)
    _login(client, user.email)
    headers = _csrf_headers(client)

    add_cart_response = _add_cart_item(client, headers, variant, 2)
    assert add_cart_response["success"] is True

    cart_payload = _get_cart(client)
    assert len(cart_payload["items"]) == 1
    assert cart_payload["subtotal"] == 2400.0
    assert cart_payload["shipping_amount"] == 0.0
    assert cart_payload["tax"] == 120.0
    assert cart_payload["total"] == 2520.0

    address_response = _create_address_via_api(
        client,
        headers,
        full_name="Parth Kaswala",
        phone="9876543210",
        address_line1="D-101 Gokuldham",
        city="Surat",
        state="Gujarat",
        pincode="394101",
    )
    address_id = address_response["id"]

    addresses_response = client.get("/api/v1/users/me/addresses")
    assert addresses_response.status_code == 200
    addresses = addresses_response.json()["data"]
    assert len(addresses) == 1
    assert addresses[0]["is_default"] is True

    checkout_response = client.post(
        "/checkout",
        headers=headers,
        json={"user_id": user.id, "address_id": address_id},
    )
    assert checkout_response.status_code == 200
    checkout_payload = checkout_response.json()["data"]
    assert checkout_payload["status"] == "validated"
    assert checkout_payload["total"] == 2520.0

    prefixed_checkout_response = client.post(
        "/api/v1/checkout",
        headers=headers,
        json={"user_id": user.id, "address_id": address_id},
    )
    assert prefixed_checkout_response.status_code == 404

    monkeypatch.setattr(
        commerce_checkout,
        "get_razorpay_client",
        lambda: _FakeRazorpayClient(),
    )

    payment_order_response = client.post(
        "/create-payment-order",
        headers=headers,
        json={"user_id": user.id, "address_id": address_id},
    )
    assert payment_order_response.status_code == 200
    payment_order_payload = payment_order_response.json()["data"]
    assert payment_order_payload["razorpay_order_id"] == "order_test_checkout_123"
    assert payment_order_payload["amount"] == 252000
    assert payment_order_payload["total"] == 2520.0

    created_intent = (
        db_session.query(CheckoutPaymentIntent)
        .filter(CheckoutPaymentIntent.razorpay_order_id == "order_test_checkout_123")
        .first()
    )
    assert created_intent is not None
    assert created_intent.status == CheckoutPaymentIntentStatus.PENDING

    message = "order_test_checkout_123|pay_test_checkout_123"
    signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    verify_response = client.post(
        "/verify-payment",
        headers=headers,
        json={
            "razorpay_order_id": "order_test_checkout_123",
            "razorpay_payment_id": "pay_test_checkout_123",
            "razorpay_signature": signature,
            "user_id": user.id,
            "address_id": address_id,
        },
    )
    assert verify_response.status_code == 201
    verify_body = verify_response.json()
    assert verify_body["status"] == "success"
    assert verify_body["order_id"] is not None
    verify_payload = verify_body["data"]
    assert verify_payload["payment_status"] == "success"
    assert verify_payload["order_status"] == "confirmed"
    assert verify_payload["order_id"] == verify_body["order_id"]

    assert _get_cart(client)["items"] == []

    created_order = db_session.query(Order).filter(Order.id == verify_payload["order_id"]).first()
    assert created_order is not None
    assert created_order.total_amount == 2520.0

    payment = db_session.query(Payment).filter(Payment.order_id == created_order.id).first()
    assert payment is not None
    assert payment.payment_status == PaymentStatus.SUCCESS

    db_session.refresh(created_intent)
    assert created_intent.status == CheckoutPaymentIntentStatus.SUCCESS
    assert created_intent.created_order_id == created_order.id


def test_payment_verification_accepts_raw_razorpay_payload_without_checkout_context(
    client: TestClient,
    db_session: Session,
    monkeypatch,
):
    user = _create_user(db_session, "raw-payload@example.com", "9876543315")
    variant = _create_product_bundle(db_session, "raw-payload", stock=2)
    _login(client, user.email)
    headers = _csrf_headers(client)

    _add_cart_item(client, headers, variant, 1)

    address_response = _create_address_via_api(
        client,
        headers,
        full_name="Raw Payload User",
        phone="9876543210",
        address_line1="City Light",
        city="Surat",
        state="Gujarat",
        pincode="394101",
    )
    address_id = address_response["id"]

    monkeypatch.setattr(
        commerce_checkout,
        "get_razorpay_client",
        lambda: _FakeRazorpayClient(),
    )

    payment_order_response = client.post(
        "/create-payment-order",
        headers=headers,
        json={"user_id": user.id, "address_id": address_id},
    )
    assert payment_order_response.status_code == 200

    signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        b"order_test_checkout_123|pay_raw_payload_123",
        hashlib.sha256,
    ).hexdigest()

    verify_response = client.post(
        "/verify-payment",
        headers=headers,
        json={
            "razorpay_order_id": "order_test_checkout_123",
            "razorpay_payment_id": "pay_raw_payload_123",
            "razorpay_signature": signature,
        },
    )

    assert verify_response.status_code == 201
    payload = verify_response.json()
    assert payload["status"] == "success"
    assert payload["order_id"] is not None


def test_verify_payment_returns_existing_order_id_created_by_webhook(
    client: TestClient,
    db_session: Session,
    monkeypatch,
):
    user = _create_user(db_session, "webhook-checkout@example.com", "9876543312")
    variant = _create_product_bundle(db_session, "webhook-checkout", stock=4)
    _login(client, user.email)
    headers = _csrf_headers(client)

    _add_cart_item(client, headers, variant, 1)

    address_response = _create_address_via_api(
        client,
        headers,
        full_name="Webhook User",
        phone="9876543210",
        address_line1="Ring Road",
        city="Surat",
        state="Gujarat",
        pincode="394101",
    )
    address_id = address_response["id"]

    monkeypatch.setattr(
        commerce_checkout,
        "get_razorpay_client",
        lambda: _FakeRazorpayClient(),
    )

    client.post(
        "/create-payment-order",
        headers=headers,
        json={"user_id": user.id, "address_id": address_id},
    )

    webhook_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_checkout_webhook_123",
                    "order_id": "order_test_checkout_123",
                    "amount": 141600,
                }
            }
        },
    }
    raw_body = json.dumps(webhook_payload).encode()
    signature = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    webhook_response = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "X-Razorpay-Signature": signature,
            "Content-Type": "application/json",
        },
        content=raw_body,
    )
    assert webhook_response.status_code == 200

    verify_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        b"order_test_checkout_123|pay_checkout_webhook_123",
        hashlib.sha256,
    ).hexdigest()

    verify_response = client.post(
        "/verify-payment",
        headers=headers,
        json={
            "razorpay_order_id": "order_test_checkout_123",
            "razorpay_payment_id": "pay_checkout_webhook_123",
            "razorpay_signature": verify_signature,
            "user_id": user.id,
            "address_id": address_id,
        },
    )
    assert verify_response.status_code == 201
    payload = verify_response.json()
    assert payload["status"] == "success"
    assert payload["order_id"] is not None

    created_intent = (
        db_session.query(CheckoutPaymentIntent)
        .filter(CheckoutPaymentIntent.razorpay_order_id == "order_test_checkout_123")
        .first()
    )
    assert created_intent is not None
    assert created_intent.created_order_id == payload["order_id"]


def test_address_creation_respects_default_switching(client: TestClient, db_session: Session):
    user = _create_user(db_session, "address-default@example.com", "9876543322")
    _login(client, user.email)
    headers = _csrf_headers(client)

    first = _create_address_via_api(
        client,
        headers,
        full_name="Home",
        phone="9876543210",
        address_line1="Line 1",
        city="Surat",
        state="Gujarat",
        pincode="394101",
    )

    second = _create_address_via_api(
        client,
        headers,
        full_name="Office",
        phone="9876543211",
        address_line1="Line 2",
        city="Ahmedabad",
        state="Gujarat",
        pincode="380001",
        is_default=True,
    )

    addresses = client.get("/api/v1/users/me/addresses").json()["data"]
    assert len(addresses) == 2
    assert any(address["full_name"] == "Office" and address["is_default"] is True for address in addresses)
    assert any(address["full_name"] == "Home" and address["is_default"] is False for address in addresses)

def test_structured_cart_update_and_delete_flow(client: TestClient, db_session: Session):
    user = _create_user(db_session, "root-cart-update@example.com", "9876543333")
    variant = _create_product_bundle(db_session, "root-cart-update", stock=5)
    _login(client, user.email)
    headers = _csrf_headers(client)

    add_response = _add_cart_item(client, headers, variant, 1)
    cart_item_id = add_response["data"]["cart_item_id"]

    initial_cart_payload = _get_cart(client)
    assert initial_cart_payload["subtotal"] == 1200.0
    assert initial_cart_payload["shipping_amount"] == 100.0
    assert initial_cart_payload["tax"] == 65.0
    assert initial_cart_payload["total"] == 1365.0

    update_response = client.put(
        f"/api/v1/cart/items/{cart_item_id}",
        headers=headers,
        json={"quantity": 3},
    )
    assert update_response.status_code == 200

    cart_payload = _get_cart(client)
    assert cart_payload["items"][0]["variant_id"] == variant.id
    assert cart_payload["items"][0]["quantity"] == 3

    delete_response = client.delete(
        f"/api/v1/cart/items/{cart_item_id}",
        headers=headers,
    )
    assert delete_response.status_code == 200

    empty_cart = _get_cart(client)
    assert empty_cart["items"] == []


def test_cart_clear_without_trailing_slash(client: TestClient, db_session: Session):
    user = _create_user(db_session, "cart-clear-root@example.com", "9876543388")
    variant = _create_product_bundle(db_session, "cart-clear-root", stock=4)

    _login(client, user.email)
    headers = _csrf_headers(client)
    _add_cart_item(client, headers, variant, 2)

    response = client.delete("/api/v1/cart", headers=headers)
    assert response.status_code == 200
    assert response.json()["message"] == "Cart cleared"
    assert _get_cart(client)["items"] == []


def test_create_payment_order_validates_shiprocket_serviceability_when_configured(client: TestClient, db_session: Session, monkeypatch):
    user = _create_user(db_session, "shiprocket-check@example.com", "9876543389")
    variant = _create_product_bundle(db_session, "shiprocket-check", stock=2)
    address = _create_address_via_api

    _login(client, user.email)
    headers = _csrf_headers(client)
    _add_cart_item(client, headers, variant, 1)
    saved_address = address(
        client,
        headers,
        full_name="Checkout User",
        phone="9876543210",
        address_line1="Street 1",
        city="Surat",
        state="Gujarat",
        pincode="395007",
    )

    monkeypatch.setattr(commerce_checkout, "validate_shiprocket_configuration", lambda strict=False: True)

    def _raise_not_serviceable(*args, **kwargs):
        raise RuntimeError("Delivery pincode 395007 is not serviceable")

    monkeypatch.setattr(commerce_checkout, "check_pincode_serviceability", _raise_not_serviceable)

    response = client.post(
        "/create-payment-order",
        headers=headers,
        json={
            "user_id": user.id,
            "address_id": saved_address["id"],
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Delivery pincode 395007 is not serviceable"


def test_cart_user_route_requires_authenticated_ownership(client: TestClient, db_session: Session):
    user = _create_user(db_session, "cart-owner@example.com", "9876543393")
    other_user = _create_user(db_session, "cart-other@example.com", "9876543394")
    variant = _create_product_bundle(db_session, "cart-owner", stock=3)

    _login(client, user.email)
    headers = _csrf_headers(client)
    _add_cart_item(client, headers, variant, 1)

    own_cart_response = client.get(f"/api/v1/cart/user/{user.id}")
    assert own_cart_response.status_code == 200
    assert own_cart_response.json()["data"]["items"][0]["variant_id"] == variant.id

    forbidden_response = client.get(f"/api/v1/cart/user/{other_user.id}")
    assert forbidden_response.status_code == 403


def test_direct_order_creation_endpoint_is_removed(client: TestClient, db_session: Session):
    user = _create_user(db_session, "order-disabled@example.com", "9876543344")
    _login(client, user.email)
    headers = _csrf_headers(client)

    response = client.post(
        "/orders",
        headers=headers,
        json={"user_id": user.id, "address_id": 1},
    )
    assert response.status_code == 404


def test_checkout_payment_endpoints_require_authenticated_ownership(
    client: TestClient,
    db_session: Session,
):
    user = _create_user(db_session, "ownership@example.com", "9876543391")
    other_user = _create_user(db_session, "ownership-other@example.com", "9876543392")
    variant = _create_product_bundle(db_session, "ownership", stock=4)

    _login(client, user.email)
    headers = _csrf_headers(client)

    _add_cart_item(client, headers, variant, 1)

    address = Address(
        user_id=user.id,
        full_name="Owner User",
        phone="9876543210",
        address_line1="Line 1",
        city="Surat",
        state="Gujarat",
        pincode="395007",
        country="India",
        is_default=True,
        address_type="home",
    )
    db_session.add(address)
    db_session.commit()
    db_session.refresh(address)

    forbidden_checkout = client.post(
        "/checkout",
        headers=headers,
        json={"user_id": other_user.id, "address_id": address.id},
    )
    assert forbidden_checkout.status_code == 403

    forbidden_payment_order = client.post(
        "/create-payment-order",
        headers=headers,
        json={"user_id": other_user.id, "address_id": address.id},
    )
    assert forbidden_payment_order.status_code == 403

import hashlib
import hmac
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.api.v1 import webhooks
from app.models.address import Address
from app.models.category import Category
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import Product, ProductVariant
from app.models.return_request import ReturnRequest, ReturnStatus
from app.models.user import User, UserRole
from app.services import shiprocket
from app.services.return_service import utc_now


def _csrf_headers(client: TestClient) -> dict:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def _create_user(db: Session, email: str, phone: str, role: UserRole = UserRole.CUSTOMER) -> User:
    user = User(
        email=email,
        full_name="Return Test User",
        phone=phone,
        password_hash=hash_password("StrongPass1"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_address(db: Session, user_id: int, suffix: str) -> Address:
    address = Address(
        user_id=user_id,
        full_name=f"Return User {suffix}",
        phone=f"9876543{suffix[-3:]}",
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


def _create_variant(db: Session, suffix: str, stock: int = 3) -> ProductVariant:
    category = Category(name=f"Return Category {suffix}", slug=f"return-category-{suffix}", is_active=True)
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name=f"Return Product {suffix}",
        slug=f"return-product-{suffix}",
        base_price=999.0,
        is_active=True,
        is_featured=False,
    )
    db.add(product)
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        size="L",
        color="Gold",
        sku=f"RETURN-SKU-{suffix}",
        stock_quantity=stock,
        additional_price=0.0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _create_delivered_order(db: Session, user_id: int, variant: ProductVariant, suffix: str) -> Order:
    shipping_address = _create_address(db, user_id, f"{suffix}1")
    billing_address = _create_address(db, user_id, f"{suffix}2")
    delivered_at = utc_now()

    order = Order(
        order_number=f"RETURN-{suffix}",
        user_id=user_id,
        subtotal=999.0,
        tax_amount=179.82,
        shipping_charge=0.0,
        total_amount=1178.82,
        status=OrderStatus.DELIVERED,
        shipping_address_id=shipping_address.id,
        billing_address_id=billing_address.id,
        stock_deducted=True,
        delivered_at=delivered_at,
        return_deadline=delivered_at + timedelta(hours=36),
        return_status="eligible",
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            product_name="Return Product",
            variant_details="Size: L, Color: Gold",
            quantity=1,
            unit_price=999.0,
            total_price=999.0,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def test_return_eligibility_uses_server_time(client: TestClient, db_session: Session):
    user = _create_user(db_session, "return-eligibility@example.com", "9876543401")
    variant = _create_variant(db_session, "eligibility")
    order = _create_delivered_order(db_session, user.id, variant, "ELIGIBLE")

    _login(client, user.email)
    response = client.get(
        f"/api/v1/orders/{order.id}/return-eligibility",
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["eligible"] is True
    assert payload["ms_remaining"] > 0
    assert payload["server_time"].endswith("Z")
    assert payload["return_deadline"].endswith("Z")


def test_create_return_request_sets_user_id_and_blocks_duplicates(client: TestClient, db_session: Session):
    user = _create_user(db_session, "return-request@example.com", "9876543402")
    variant = _create_variant(db_session, "request")
    order = _create_delivered_order(db_session, user.id, variant, "REQUEST")
    order_item = order.items[0]

    _login(client, user.email)
    headers = _csrf_headers(client)

    first = client.post(
        "/api/v1/returns/",
        headers=headers,
        json={
            "order_id": order.id,
            "order_item_id": order_item.id,
            "reason": "other",
            "description": "Need to return the item",
        },
    )
    assert first.status_code == 200

    created_return = db_session.query(ReturnRequest).filter(ReturnRequest.order_item_id == order_item.id).first()
    assert created_return is not None
    assert created_return.user_id == user.id

    second = client.post(
        "/api/v1/returns/",
        headers=headers,
        json={
            "order_id": order.id,
            "order_item_id": order_item.id,
            "reason": "other",
            "description": "Duplicate request",
        },
    )
    assert second.status_code == 409


def test_expired_return_window_is_rejected(client: TestClient, db_session: Session):
    user = _create_user(db_session, "return-expired@example.com", "9876543403")
    variant = _create_variant(db_session, "expired")
    order = _create_delivered_order(db_session, user.id, variant, "EXPIRED")
    order.return_deadline = utc_now() - timedelta(minutes=1)
    order.return_status = "eligible"
    db_session.commit()

    _login(client, user.email)
    response = client.post(
        "/api/v1/returns/",
        headers=_csrf_headers(client),
        json={
            "order_id": order.id,
            "order_item_id": order.items[0].id,
            "reason": "other",
            "description": "Late request",
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Return window expired"


def test_authenticated_order_detail_by_order_number(client: TestClient, db_session: Session):
    user = _create_user(db_session, "public-tracking@example.com", "9876543490")
    variant = _create_variant(db_session, "publictracking")
    order = _create_delivered_order(db_session, user.id, variant, "PUBTRACK")
    order.tracking_url = "https://tracking.example.com/ship/123"
    order.courier_name = "Shiprocket Express"
    db_session.commit()

    _login(client, user.email)
    response = client.get(f"/api/v1/orders/{order.order_number}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["order"]["order_number"] == order.order_number
    assert payload["order"]["public_status"] == "DELIVERED"
    assert payload["order"]["tracking_url"] == "https://tracking.example.com/ship/123"
    assert payload["order"]["timeline"][0]["status"] == "PLACED"


def test_public_tracking_endpoint_returns_tracking_payload(client: TestClient, db_session: Session):
    user = _create_user(db_session, "public-tracking-endpoint@example.com", "9876543492")
    variant = _create_variant(db_session, "publictrackingendpoint")
    order = _create_delivered_order(db_session, user.id, variant, "PUBTRACK2")
    order.awb_code = "AWB123456"
    order.courier_name = "Shiprocket Express"
    order.current_location = "Surat Hub"
    db_session.commit()

    response = client.get(f"/api/v1/orders/{order.order_number}/tracking")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["awb_code"] == "AWB123456"
    assert payload["data"]["location"] == "Surat Hub"
    assert payload["order"]["order_number"] == order.order_number


def test_order_level_return_endpoint_marks_tracking_status(client: TestClient, db_session: Session):
    user = _create_user(db_session, "order-return@example.com", "9876543491")
    variant = _create_variant(db_session, "orderreturn")
    order = _create_delivered_order(db_session, user.id, variant, "ORDERRET")

    _login(client, user.email)
    response = client.post(
        f"/api/v1/orders/{order.id}/return",
        headers=_csrf_headers(client),
        json={"reason": "other", "description": "Need a return"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "RETURN_REQUESTED"

    db_session.refresh(order)
    assert order.status == OrderStatus.RETURN_REQUESTED
    assert order.return_status == "requested"


def test_new_webhook_endpoint_confirms_payment(client: TestClient, db_session: Session):
    user = _create_user(db_session, "webhook-v2@example.com", "9876543404")
    variant = _create_variant(db_session, "webhook", stock=1)
    order = _create_delivered_order(db_session, user.id, variant, "WEBHOOK")
    order.status = OrderStatus.PENDING
    order.delivered_at = None
    order.return_deadline = None
    order.return_status = "not_applicable"
    order.stock_deducted = True

    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.PENDING,
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id="rzp_order_new_webhook",
    )
    db_session.add(payment)
    db_session.commit()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_new_webhook",
                    "order_id": "rzp_order_new_webhook",
                    "amount": 117882,
                }
            }
        },
    }
    raw_body = json.dumps(payload).encode()
    signature = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "X-Razorpay-Signature": signature,
            "Content-Type": "application/json",
        },
        content=raw_body,
    )

    assert response.status_code == 200
    db_session.refresh(payment)
    db_session.refresh(order)
    assert payment.payment_status == PaymentStatus.SUCCESS
    assert order.status == OrderStatus.CONFIRMED


def test_delivered_order_cannot_move_back_to_processing(client: TestClient, db_session: Session):
    user = _create_user(db_session, "order-transition-user@example.com", "9876543405")
    admin = _create_user(
        db_session,
        "order-transition-admin@example.com",
        "9876543406",
        role=UserRole.ADMIN,
    )
    variant = _create_variant(db_session, "transition")
    order = _create_delivered_order(db_session, user.id, variant, "TRANSITION")

    _login(client, admin.email)
    response = client.put(
        f"/api/v1/orders/{order.id}/status",
        headers=_csrf_headers(client),
        json={"status": "processing"},
    )

    assert response.status_code == 400
    assert "Cannot transition order" in response.json()["message"]


def test_shiprocket_webhook_updates_forward_delivery_status(client: TestClient, db_session: Session):
    user = _create_user(db_session, "shiprocket-forward@example.com", "9876543410")
    variant = _create_variant(db_session, "shiprocket-forward")
    order = _create_delivered_order(db_session, user.id, variant, "SHIPROCKETFWD")
    order.status = OrderStatus.SHIPPED
    order.shipment_id = "ship-123"
    order.awb_code = "awb-123"
    db_session.commit()

    secret = settings.SHIPROCKET_WEBHOOK_SECRET
    settings.SHIPROCKET_WEBHOOK_SECRET = "shiprocket-test-secret"

    payload = {
        "shipment_id": "ship-123",
        "awb_code": "awb-123",
        "status": "out_for_delivery",
        "courier_name": "Shiprocket Express",
        "tracking_url": "https://tracking.example.com/fwd",
        "current_location": "Surat Hub",
    }
    raw_body = json.dumps(payload).encode()
    signature = hmac.new(
        settings.SHIPROCKET_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/api/v1/shiprocket/webhook",
        headers={
            "X-Shiprocket-Signature": signature,
            "Content-Type": "application/json",
        },
        content=raw_body,
    )

    settings.SHIPROCKET_WEBHOOK_SECRET = secret

    assert response.status_code == 200
    db_session.refresh(order)
    assert order.status == OrderStatus.OUT_FOR_DELIVERY
    assert order.courier_name == "Shiprocket Express"
    assert order.tracking_url == "https://tracking.example.com/fwd"
    assert order.current_location == "Surat Hub"


def test_shiprocket_webhook_updates_return_request_status(client: TestClient, db_session: Session):
    user = _create_user(db_session, "shiprocket-return@example.com", "9876543411")
    variant = _create_variant(db_session, "shiprocket-return")
    order = _create_delivered_order(db_session, user.id, variant, "SHIPROCKETRET")

    return_request = ReturnRequest(
        order_id=order.id,
        order_item_id=order.items[0].id,
        user_id=user.id,
        reason="other",
        description="Return requested",
        refund_amount=999.0,
        refund_method="original_payment",
        shiprocket_return_order_id="RET-SHIPROCKETRET",
        shiprocket_return_shipment_id="return-ship-123",
        status=ReturnStatus.REQUESTED,
    )
    db_session.add(return_request)
    db_session.commit()

    secret = settings.SHIPROCKET_WEBHOOK_SECRET
    settings.SHIPROCKET_WEBHOOK_SECRET = "shiprocket-test-secret"

    payload = {
        "shipment_id": "return-ship-123",
        "status": "picked_up",
        "courier_name": "Shiprocket Reverse",
        "tracking_url": "https://tracking.example.com/ret",
        "awb_code": "ret-awb-123",
    }
    raw_body = json.dumps(payload).encode()
    signature = hmac.new(
        settings.SHIPROCKET_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/api/v1/shiprocket/webhook",
        headers={
            "X-Shiprocket-Signature": signature,
            "Content-Type": "application/json",
        },
        content=raw_body,
    )

    settings.SHIPROCKET_WEBHOOK_SECRET = secret

    assert response.status_code == 200
    db_session.refresh(return_request)
    assert return_request.status == ReturnStatus.PICKED_UP
    assert return_request.return_courier_name == "Shiprocket Reverse"
    assert return_request.return_tracking_url == "https://tracking.example.com/ret"
    assert return_request.return_awb_code == "ret-awb-123"


def test_fulfill_order_checks_shiprocket_serviceability(monkeypatch, db_session: Session):
    user = _create_user(db_session, "shiprocket-serviceability@example.com", "9876543412")
    variant = _create_variant(db_session, "shiprocket-serviceability")
    order = _create_delivered_order(db_session, user.id, variant, "SERVICEABLE")
    order.status = OrderStatus.CONFIRMED
    order.shipment_id = None
    order.awb_code = None

    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        payment_status=PaymentStatus.SUCCESS,
        amount=order.total_amount,
        currency="INR",
    )
    db_session.add(payment)
    db_session.commit()
    db_session.refresh(order)

    original_email = settings.SHIPROCKET_EMAIL
    original_password = settings.SHIPROCKET_PASSWORD
    original_pickup_postcode = settings.SHIPROCKET_PICKUP_POSTCODE
    settings.SHIPROCKET_EMAIL = "ops@example.com"
    settings.SHIPROCKET_PASSWORD = "secret"
    settings.SHIPROCKET_PICKUP_POSTCODE = "395007"

    called = {"serviceability": False}

    def fake_check(pincode: str, *, cod: bool = False):
        called["serviceability"] = True
        assert pincode == order.shipping_address.pincode
        assert cod is False
        return {"serviceable": True, "skipped": False}

    monkeypatch.setattr(shiprocket, "check_pincode_serviceability", fake_check)
    monkeypatch.setattr(
        shiprocket,
        "create_forward_shipment",
        lambda _: shiprocket.ShiprocketShipmentResult(
            shiprocket_order_id="sr-order-1",
            shipment_id="shipment-1",
            awb_code="awb-1",
            courier_name="Shiprocket Express",
            tracking_url="https://tracking.example.com",
            expected_delivery=None,
            current_status="SHIPPED",
            current_location="Warehouse",
            raw_response={},
        ),
    )
    monkeypatch.setattr(
        shiprocket,
        "assign_awb",
        lambda _: shiprocket.ShiprocketShipmentResult(
            shiprocket_order_id="sr-order-1",
            shipment_id="shipment-1",
            awb_code="awb-1",
            courier_name="Shiprocket Express",
            tracking_url="https://tracking.example.com",
            expected_delivery=None,
            current_status="SHIPPED",
            current_location="Warehouse",
            raw_response={},
        ),
    )
    monkeypatch.setattr(shiprocket, "generate_pickup", lambda _: {"pickup_status": "generated"})
    monkeypatch.setattr(shiprocket, "sync_order_tracking", lambda _: None)

    result = shiprocket.fulfill_order(order)

    settings.SHIPROCKET_EMAIL = original_email
    settings.SHIPROCKET_PASSWORD = original_password
    settings.SHIPROCKET_PICKUP_POSTCODE = original_pickup_postcode

    assert called["serviceability"] is True
    assert result["fulfilled"] is True

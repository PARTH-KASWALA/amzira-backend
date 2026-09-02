from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.address import Address
from app.models.category import Category
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import Product, ProductVariant
from app.models.user import User, UserRole
from app.services.order_service import auto_cancel_pending_orders


def _create_user(
    db: Session,
    email: str,
    phone: str,
    *,
    role: UserRole = UserRole.CUSTOMER,
) -> User:
    user = User(
        email=email,
        phone=phone,
        full_name="Seller Panel User",
        password_hash=hash_password("StrongPass1"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_address(db: Session, user_id: int) -> Address:
    address = Address(
        user_id=user_id,
        full_name="Delivery Customer",
        phone="9876543200",
        address_line1="10 Temple Street",
        address_line2="Near Market",
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


def _create_variant(db: Session, suffix: str, stock: int = 5) -> ProductVariant:
    category = Category(name=f"Seller {suffix}", slug=f"seller-{suffix}", is_active=True)
    db.add(category)
    db.flush()
    product = Product(
        category_id=category.id,
        name=f"Seller Product {suffix}",
        slug=f"seller-product-{suffix}",
        base_price=1000,
        is_active=True,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Maroon",
        sku=f"SELLER-{suffix.upper()}",
        stock_quantity=stock,
        additional_price=0,
        is_active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _create_order(
    db: Session,
    user: User,
    variant: ProductVariant,
    *,
    suffix: str,
    status: OrderStatus = OrderStatus.CONFIRMED,
    payment_method: PaymentMethod = PaymentMethod.RAZORPAY,
    payment_status: PaymentStatus = PaymentStatus.SUCCESS,
    stock_deducted: bool = True,
    expires_at: datetime | None = None,
) -> Order:
    address = _create_address(db, user.id)
    order = Order(
        order_number=f"AMZ-SELLER-{suffix}",
        user_id=user.id,
        subtotal=1000,
        tax_amount=50,
        shipping_charge=100,
        discount_amount=0,
        total_amount=1150,
        status=status,
        stock_deducted=stock_deducted,
        expires_at=expires_at,
        shipping_address_id=address.id,
        billing_address_id=address.id,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            product_name=variant.product.name,
            variant_details="Size: M, Color: Maroon",
            quantity=1,
            unit_price=1000,
            total_price=1000,
        )
    )
    db.add(
        Payment(
            order_id=order.id,
            payment_method=payment_method,
            payment_status=payment_status,
            amount=1150,
            currency="INR",
            razorpay_order_id=f"order_{suffix}" if payment_method == PaymentMethod.RAZORPAY else None,
            razorpay_payment_id=f"pay_{suffix}" if payment_status == PaymentStatus.SUCCESS else None,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPass1"},
    )
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/auth/csrf-token")
    assert response.status_code == 200
    token = response.cookies.get("csrf_token")
    assert token
    return {"X-CSRF-Token": token}


def test_checkout_kill_switch_blocks_payment_order_creation(
    client: TestClient,
    db_session: Session,
):
    user = _create_user(db_session, "checkout-off@example.com", "9876543111")
    _login(client, user.email)
    previous = settings.CHECKOUT_ENABLED
    settings.CHECKOUT_ENABLED = False
    try:
        status_response = client.get("/api/v1/commerce/status")
        assert status_response.status_code == 200
        assert status_response.json()["data"] == {
            "checkout_enabled": False,
            "cod_enabled": False,
        }

        response = client.post(
            "/api/v1/create-payment-order",
            headers=_csrf_headers(client),
            json={"user_id": user.id, "address_id": 999999},
        )
        assert response.status_code == 503
        assert response.json()["message"] == "Checkout is temporarily unavailable"
    finally:
        settings.CHECKOUT_ENABLED = previous


def test_cod_requires_explicit_feature_enablement(client: TestClient, db_session: Session):
    user = _create_user(db_session, "cod-off@example.com", "9876543112")
    _login(client, user.email)
    previous_checkout = settings.CHECKOUT_ENABLED
    previous_cod = settings.COD_ENABLED
    settings.CHECKOUT_ENABLED = True
    settings.COD_ENABLED = False
    try:
        response = client.post(
            "/api/v1/orders/",
            headers=_csrf_headers(client),
            json={
                "shipping_address_id": 999999,
                "billing_address_id": 999999,
                "payment_method": "cod",
                "idempotency_key": str(uuid4()),
            },
        )
        assert response.status_code == 405
        assert response.json()["message"] == "Cash on Delivery is not available"
    finally:
        settings.CHECKOUT_ENABLED = previous_checkout
        settings.COD_ENABLED = previous_cod


def test_cod_order_without_expiry_is_not_auto_cancelled(db_session: Session):
    user = _create_user(db_session, "cod-survives@example.com", "9876543113")
    variant = _create_variant(db_session, "cod-survives", stock=4)
    order = _create_order(
        db_session,
        user,
        variant,
        suffix="COD-SURVIVES",
        status=OrderStatus.PLACED,
        payment_method=PaymentMethod.COD,
        payment_status=PaymentStatus.PENDING,
        expires_at=None,
    )
    order.created_at = datetime.utcnow() - timedelta(hours=2)
    db_session.commit()

    assert auto_cancel_pending_orders(db_session) == 0
    db_session.refresh(order)
    db_session.refresh(variant)
    assert order.status == OrderStatus.PLACED
    assert order.stock_deducted is True
    assert variant.stock_quantity == 4


def test_explicitly_expired_order_is_cancelled_and_restocked(db_session: Session):
    user = _create_user(db_session, "expired-order@example.com", "9876543114")
    variant = _create_variant(db_session, "expired", stock=0)
    order = _create_order(
        db_session,
        user,
        variant,
        suffix="EXPIRED",
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    )

    assert auto_cancel_pending_orders(db_session) == 1
    db_session.refresh(order)
    db_session.refresh(variant)
    assert order.status == OrderStatus.CANCELLED
    assert order.stock_deducted is False
    assert variant.stock_quantity == 1


def test_customer_cannot_read_seller_orders(client: TestClient, db_session: Session):
    customer = _create_user(db_session, "seller-denied@example.com", "9876543115")
    _login(client, customer.email)

    response = client.get("/api/v1/admin/orders")
    assert response.status_code == 403
    assert response.json()["message"] == "Admin access required"


def test_sensitive_order_responses_are_never_cacheable(client: TestClient):
    response = client.get("/api/v1/admin/orders")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_admin_can_filter_list_and_read_complete_order_detail(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        "seller-admin@example.com",
        "9876543116",
        role=UserRole.ADMIN,
    )
    customer = _create_user(db_session, "buyer@example.com", "9876543117")
    variant = _create_variant(db_session, "detail", stock=4)
    order = _create_order(db_session, customer, variant, suffix="DETAIL")
    order.tracking_number = "TRACK-DETAIL"
    order.courier_name = "Delhivery"
    order.carrier_name = "Delhivery"
    db_session.commit()

    _login(client, admin.email)
    list_response = client.get(
        "/api/v1/admin/orders",
        params={"payment_status": "success", "search": "buyer@example.com", "limit": 10},
    )
    assert list_response.status_code == 200
    payload = list_response.json()["data"]
    assert payload["total"] == 1
    assert payload["pages"] == 1
    assert payload["orders"][0]["order_number"] == order.order_number
    assert payload["orders"][0]["payment_status"] == "success"
    assert payload["orders"][0]["courier_name"] == "Delhivery"

    detail_response = client.get(f"/api/v1/admin/orders/{order.id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["customer"]["email"] == customer.email
    assert detail["shipping_address"]["pincode"] == "395007"
    assert detail["payment"]["transaction_reference"] == "pay_DETAIL"
    assert detail["tracking_number"] == "TRACK-DETAIL"
    assert detail["allowed_next_statuses"] == ["cancelled", "delivered", "out_for_delivery", "processing", "shipped"]

    export_response = client.get("/api/v1/admin/orders/export")
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/csv")
    assert order.order_number in export_response.text


def test_admin_cancels_unpaid_order_with_exactly_once_restock(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        "cancel-admin@example.com",
        "9876543118",
        role=UserRole.ADMIN,
    )
    customer = _create_user(db_session, "cancel-buyer@example.com", "9876543119")
    variant = _create_variant(db_session, "cancel", stock=0)
    order = _create_order(
        db_session,
        customer,
        variant,
        suffix="CANCEL",
        payment_method=PaymentMethod.COD,
        payment_status=PaymentStatus.PENDING,
    )

    _login(client, admin.email)
    headers = _csrf_headers(client)
    payload = {"status": "cancelled", "notes": "Customer requested cancellation"}
    first = client.put(f"/api/v1/admin/orders/{order.id}/status", headers=headers, json=payload)
    second = client.put(f"/api/v1/admin/orders/{order.id}/status", headers=headers, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200

    db_session.refresh(order)
    db_session.refresh(variant)
    assert order.status == OrderStatus.CANCELLED
    assert order.stock_deducted is False
    assert order.admin_notes == "Customer requested cancellation"
    assert variant.stock_quantity == 1
    assert len(order.status_history) == 1
    assert order.status_history[0].changed_by == admin.id


def test_admin_cannot_status_only_cancel_successful_razorpay_order(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        "paid-cancel-admin@example.com",
        "9876543120",
        role=UserRole.ADMIN,
    )
    customer = _create_user(db_session, "paid-cancel-buyer@example.com", "9876543121")
    variant = _create_variant(db_session, "paid-cancel", stock=0)
    order = _create_order(db_session, customer, variant, suffix="PAID-CANCEL")

    _login(client, admin.email)
    response = client.put(
        f"/api/v1/admin/orders/{order.id}/status",
        headers=_csrf_headers(client),
        json={"status": "cancelled", "notes": "Do not bypass the refund"},
    )
    assert response.status_code == 409
    assert "require a refund" in response.json()["message"]

    db_session.refresh(order)
    db_session.refresh(variant)
    assert order.status == OrderStatus.CONFIRMED
    assert order.stock_deducted is True
    assert variant.stock_quantity == 0


def test_delivered_status_is_idempotent_and_preserves_return_window(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        "deliver-admin@example.com",
        "9876543122",
        role=UserRole.ADMIN,
    )
    customer = _create_user(db_session, "deliver-buyer@example.com", "9876543123")
    variant = _create_variant(db_session, "deliver", stock=0)
    order = _create_order(
        db_session,
        customer,
        variant,
        suffix="DELIVER",
        status=OrderStatus.OUT_FOR_DELIVERY,
    )

    _login(client, admin.email)
    headers = _csrf_headers(client)
    payload = {"status": "delivered", "notes": "Delivered to customer"}
    first = client.put(f"/api/v1/admin/orders/{order.id}/status", headers=headers, json=payload)
    assert first.status_code == 200
    db_session.refresh(order)
    delivered_at = order.delivered_at
    return_deadline = order.return_deadline

    second = client.put(f"/api/v1/admin/orders/{order.id}/status", headers=headers, json=payload)
    assert second.status_code == 200
    db_session.refresh(order)
    assert order.delivered_at == delivered_at
    assert order.return_deadline == return_deadline
    assert len(order.status_history) == 1


def test_shipment_requires_tracking_and_customer_history_hides_admin_notes(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        "tracking-admin@example.com",
        "9876543124",
        role=UserRole.ADMIN,
    )
    customer = _create_user(db_session, "tracking-buyer@example.com", "9876543125")
    variant = _create_variant(db_session, "tracking", stock=0)
    order = _create_order(
        db_session,
        customer,
        variant,
        suffix="TRACKING",
        status=OrderStatus.PROCESSING,
    )

    _login(client, admin.email)
    headers = _csrf_headers(client)
    rejected = client.put(
        f"/api/v1/admin/orders/{order.id}/status",
        headers=headers,
        json={"status": "shipped", "notes": "Internal packing observation"},
    )
    assert rejected.status_code == 422

    shipped = client.put(
        f"/api/v1/admin/orders/{order.id}/status",
        headers=headers,
        json={
            "status": "shipped",
            "tracking_number": "TRACK-PRIVATE-1",
            "carrier_name": "Delhivery",
            "notes": "Internal packing observation",
        },
    )
    assert shipped.status_code == 200

    _login(client, customer.email)
    customer_tracking = client.get("/api/v1/orders/my/tracking")
    assert customer_tracking.status_code == 200
    history = customer_tracking.json()["data"][0]["status_history"][0]
    assert history["changed_by"] is None
    assert history["changer_name"] is None
    assert history["notes"] is None

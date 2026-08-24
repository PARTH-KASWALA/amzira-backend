import json
import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.utils.order_utils import generate_order_number
from app.models.address import Address
from app.models.cart import CartItem
from app.models.checkout_payment_intent import (
    CheckoutPaymentIntent,
    CheckoutPaymentIntentStatus,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.coupon import Coupon, DiscountType
from app.models.coupon_usage import CouponUsage
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import ProductVariant
from app.core.pricing import money
from app.services.return_service import utc_now


def build_cart_fingerprint(*, user_id: int, address_id: int, items: list[dict], coupon_code: str | None = None) -> str:
    canonical = json.dumps(
        {"user_id": user_id, "address_id": address_id, "items": items, "coupon_code": coupon_code},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def quote_checkout_coupon(
    db: Session,
    *,
    user_id: int,
    coupon_code: str,
    subtotal: Decimal,
) -> tuple[Coupon, Decimal]:
    coupon = (
        db.query(Coupon)
        .filter(Coupon.code == coupon_code, Coupon.is_active == True)
        .with_for_update()
        .first()
    )
    if not coupon:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or inactive coupon code")
    if coupon.expiry_date and coupon.expiry_date < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon has expired")
    if subtotal < money(coupon.min_order_value):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart does not meet the coupon minimum")
    if coupon.usage_limit and (coupon.used_count + coupon.reserved_count) >= coupon.usage_limit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon usage limit has been reached")

    used_by_user = db.query(CouponUsage.id).filter(
        CouponUsage.coupon_id == coupon.id,
        CouponUsage.user_id == user_id,
    ).count()
    reserved_by_user = db.query(CheckoutPaymentIntent.id).filter(
        CheckoutPaymentIntent.coupon_id == coupon.id,
        CheckoutPaymentIntent.user_id == user_id,
        CheckoutPaymentIntent.coupon_reserved == True,
        CheckoutPaymentIntent.status == CheckoutPaymentIntentStatus.PENDING,
    ).count()
    if used_by_user + reserved_by_user >= coupon.per_user_limit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon user limit has been reached")

    if coupon.discount_type == DiscountType.PERCENTAGE:
        discount = money(subtotal * money(coupon.discount_value) / Decimal("100"))
        if coupon.max_discount is not None:
            discount = min(discount, money(coupon.max_discount))
    else:
        discount = min(money(coupon.discount_value), subtotal)
    return coupon, max(money(0), discount)


def reserve_checkout_coupon(intent: CheckoutPaymentIntent, coupon: Coupon | None) -> None:
    if not coupon:
        return
    coupon.reserved_count += 1
    intent.coupon_id = coupon.id
    intent.coupon_code = coupon.code
    intent.coupon_reserved = True


def release_checkout_coupon(db: Session, intent: CheckoutPaymentIntent) -> bool:
    if not intent.coupon_reserved or not intent.coupon_id:
        return False
    coupon = db.query(Coupon).filter(Coupon.id == intent.coupon_id).with_for_update().first()
    if coupon:
        coupon.reserved_count = max(0, coupon.reserved_count - 1)
    intent.coupon_reserved = False
    return True


def consume_checkout_coupon(db: Session, intent: CheckoutPaymentIntent, order: Order) -> None:
    if not intent.coupon_id or not intent.coupon_code:
        return
    existing = db.query(CouponUsage.id).filter(CouponUsage.order_id == order.id).first()
    if existing:
        intent.coupon_reserved = False
        return
    coupon = db.query(Coupon).filter(Coupon.id == intent.coupon_id).with_for_update().first()
    if not coupon:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reserved coupon is unavailable")
    if intent.coupon_reserved:
        coupon.reserved_count = max(0, coupon.reserved_count - 1)
    coupon.used_count += 1
    intent.coupon_reserved = False
    order.coupon_code = intent.coupon_code
    order.discount_amount = money(intent.discount_amount)
    db.add(CouponUsage(coupon_id=coupon.id, user_id=intent.user_id, order_id=order.id))


def _snapshot_items(intent: CheckoutPaymentIntent) -> list[dict]:
    try:
        items = json.loads(intent.cart_snapshot or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment cart snapshot is invalid",
        ) from exc
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment cart snapshot is empty")
    return items


def build_checkout_payment_response(order: Order, message: str) -> dict:
    return {
        "status": "success",
        "order_id": order.id,
        "order_number": order.order_number,
        "payment_status": PaymentStatus.SUCCESS.value,
        "order_status": order.status.value if isinstance(order.status, OrderStatus) else str(order.status),
        "message": message,
    }


def get_payment_mapped_order(
    db: Session,
    *,
    razorpay_payment_id: str,
    razorpay_order_id: str | None = None,
) -> Order | None:
    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_payment_id == razorpay_payment_id)
        .first()
    )
    if payment is None or payment.order is None:
        return None
    if razorpay_order_id and payment.razorpay_order_id != razorpay_order_id:
        return None
    return payment.order


def sync_intent_success(
    intent: CheckoutPaymentIntent,
    *,
    order_id: int,
    razorpay_payment_id: str,
    razorpay_signature: str | None,
) -> None:
    intent.status = CheckoutPaymentIntentStatus.SUCCESS
    intent.created_order_id = order_id
    intent.razorpay_payment_id = razorpay_payment_id
    if razorpay_signature:
        intent.razorpay_signature = razorpay_signature
    intent.stock_reserved = False
    intent.reservation_consumed_at = datetime.utcnow()
    intent.failure_reason = None


def _get_locked_address(db: Session, intent: CheckoutPaymentIntent) -> Address:
    address = (
        db.query(Address)
        .filter(Address.id == intent.address_id, Address.user_id == intent.user_id)
        .first()
    )
    if not address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    return address


def _lock_variants_for_snapshot(db: Session, items: list[dict]) -> dict[int, ProductVariant]:
    requested_quantities: dict[int, int] = {}
    for item in items:
        variant_id = int(item["variant_id"])
        requested_quantities[variant_id] = requested_quantities.get(variant_id, 0) + int(item["quantity"])

    locked_variants: dict[int, ProductVariant] = {}
    for variant_id in sorted(requested_quantities.keys()):
        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == variant_id)
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found")
        if variant.stock_quantity < requested_quantities[variant_id]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for variant {variant.id}",
            )
        locked_variants[variant_id] = variant

    return locked_variants


def reserve_checkout_stock(db: Session, intent: CheckoutPaymentIntent) -> None:
    if intent.stock_reserved:
        return
    items = _snapshot_items(intent)
    locked_variants = _lock_variants_for_snapshot(db, items)
    for item in items:
        locked_variants[int(item["variant_id"])].stock_quantity -= int(item["quantity"])
    intent.stock_reserved = True
    intent.reservation_released_at = None
    intent.reservation_consumed_at = None
    db.flush()


def release_checkout_stock(
    db: Session,
    intent: CheckoutPaymentIntent,
    *,
    reason: str,
) -> bool:
    coupon_released = release_checkout_coupon(db, intent)
    if not intent.stock_reserved:
        return coupon_released

    items = _snapshot_items(intent)
    requested_quantities: dict[int, int] = {}
    for item in items:
        variant_id = int(item["variant_id"])
        requested_quantities[variant_id] = requested_quantities.get(variant_id, 0) + int(item["quantity"])

    variants = {
        variant.id: variant
        for variant in (
            db.query(ProductVariant)
            .filter(ProductVariant.id.in_(sorted(requested_quantities)))
            .with_for_update()
            .all()
        )
    }
    for variant_id, quantity in requested_quantities.items():
        variant = variants.get(variant_id)
        if variant is not None:
            variant.stock_quantity += quantity

    intent.stock_reserved = False
    intent.reservation_released_at = datetime.utcnow()
    intent.failure_reason = reason[:255]
    db.flush()
    return True


def expire_checkout_intents(db: Session, *, user_id: int | None = None) -> int:
    query = db.query(CheckoutPaymentIntent).filter(
        CheckoutPaymentIntent.status == CheckoutPaymentIntentStatus.PENDING,
        CheckoutPaymentIntent.expires_at.isnot(None),
        CheckoutPaymentIntent.expires_at <= datetime.utcnow(),
    )
    if user_id is not None:
        query = query.filter(CheckoutPaymentIntent.user_id == user_id)

    intents = query.order_by(CheckoutPaymentIntent.id.asc()).with_for_update().all()
    for intent in intents:
        release_checkout_stock(db, intent, reason="Checkout reservation expired")
        intent.status = CheckoutPaymentIntentStatus.EXPIRED
    return len(intents)


def refund_unfulfillable_checkout_payment(
    db: Session,
    *,
    intent: CheckoutPaymentIntent,
    razorpay_payment_id: str,
    reason: str,
) -> None:
    if intent.recovery_refund_id:
        return

    from app.services.payment_service import get_razorpay_client

    refund = get_razorpay_client().payment.refund(
        razorpay_payment_id,
        {
            "amount": int(money(intent.total_amount) * 100),
            "speed": "normal",
            "receipt": f"amzira-checkout-recovery-{intent.id}",
            "notes": {
                "checkout_intent_id": str(intent.id),
                "reason": reason[:200],
            },
        },
    )
    release_checkout_stock(db, intent, reason=reason)
    intent.status = CheckoutPaymentIntentStatus.FAILED
    intent.razorpay_payment_id = razorpay_payment_id
    intent.recovery_refund_id = refund.get("id")
    intent.recovery_refund_status = refund.get("status") or "pending"
    intent.recovery_gateway_response = json.dumps(refund)
    intent.failure_reason = reason[:255]
    db.flush()


def create_order_from_checkout_intent(
    db: Session,
    *,
    intent: CheckoutPaymentIntent,
    razorpay_payment_id: str,
    razorpay_signature: str | None,
    payment_method: PaymentMethod = PaymentMethod.RAZORPAY,
) -> Order:
    existing_order = (
        db.query(Order)
        .filter(Order.idempotency_key == intent.razorpay_order_id)
        .first()
    )
    if existing_order is not None:
        consume_checkout_coupon(db, intent, existing_order)
        sync_intent_success(
            intent,
            order_id=existing_order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        return existing_order

    if intent.created_order_id:
        existing_order = db.query(Order).filter(Order.id == intent.created_order_id).first()
        if existing_order:
            consume_checkout_coupon(db, intent, existing_order)
            sync_intent_success(
                intent,
                order_id=existing_order.id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
            )
            return existing_order

    existing_order = get_payment_mapped_order(
        db,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_order_id=intent.razorpay_order_id,
    )
    if existing_order is not None:
        consume_checkout_coupon(db, intent, existing_order)
        sync_intent_success(
            intent,
            order_id=existing_order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        return existing_order

    snapshot_items = _snapshot_items(intent)

    address = _get_locked_address(db, intent)
    reservation_held = bool(intent.stock_reserved)
    if reservation_held:
        variant_ids = sorted({int(item["variant_id"]) for item in snapshot_items})
        locked_variants = {
            variant.id: variant
            for variant in db.query(ProductVariant).filter(ProductVariant.id.in_(variant_ids)).all()
        }
        if len(locked_variants) != len(variant_ids):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reserved product variant is unavailable")
    else:
        locked_variants = _lock_variants_for_snapshot(db, snapshot_items)

    order = Order(
        order_number=generate_order_number(db),
        user_id=intent.user_id,
        subtotal=money(intent.subtotal),
        tax_amount=money(intent.tax_amount),
        shipping_charge=money(intent.shipping_amount),
        discount_amount=money(intent.discount_amount),
        coupon_code=intent.coupon_code,
        total_amount=money(intent.total_amount),
        status=OrderStatus.CONFIRMED,
        expires_at=None,
        shipping_address_id=address.id,
        billing_address_id=address.id,
        stock_deducted=True,
        idempotency_key=intent.razorpay_order_id or str(uuid4()),
        return_status="not_applicable",
    )
    db.add(order)
    db.flush()

    for item in snapshot_items:
        variant = locked_variants[int(item["variant_id"])]
        if not reservation_held:
            variant.stock_quantity -= int(item["quantity"])
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=int(item["product_id"]),
                variant_id=int(item["variant_id"]),
                product_name=item["product_name"],
                variant_details=item["variant_details"],
                quantity=int(item["quantity"]),
                unit_price=money(item["unit_price"]),
                total_price=money(item["total_price"]),
            )
        )

    payment_query = db.query(Payment)
    if payment_method == PaymentMethod.PROMOTIONAL:
        payment_query = payment_query.filter(Payment.transaction_id == razorpay_payment_id)
    else:
        payment_query = payment_query.filter(Payment.razorpay_payment_id == razorpay_payment_id)
    payment = payment_query.with_for_update().first()
    if payment is None:
        payment = Payment(
            order_id=order.id,
            payment_method=payment_method,
            payment_status=PaymentStatus.SUCCESS,
            amount=money(intent.total_amount),
            currency=intent.currency,
            razorpay_order_id=intent.razorpay_order_id if payment_method == PaymentMethod.RAZORPAY else None,
            razorpay_payment_id=razorpay_payment_id if payment_method == PaymentMethod.RAZORPAY else None,
            razorpay_signature=razorpay_signature if payment_method == PaymentMethod.RAZORPAY else None,
            transaction_id=razorpay_payment_id if payment_method == PaymentMethod.PROMOTIONAL else None,
            paid_at=utc_now(),
        )
        db.add(payment)
    else:
        payment.order_id = order.id
        payment.payment_status = PaymentStatus.SUCCESS
        payment.payment_method = payment_method
        if payment_method == PaymentMethod.RAZORPAY:
            payment.razorpay_order_id = intent.razorpay_order_id
            payment.razorpay_signature = razorpay_signature
        payment.paid_at = utc_now()

    cart_item_ids = [
        int(item["cart_item_id"])
        for item in snapshot_items
        if item.get("cart_item_id") is not None
    ]
    if cart_item_ids:
        (
            db.query(CartItem)
            .filter(CartItem.user_id == intent.user_id, CartItem.id.in_(cart_item_ids))
            .delete(synchronize_session=False)
        )

    consume_checkout_coupon(db, intent, order)
    sync_intent_success(
        intent,
        order_id=order.id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=razorpay_signature,
    )
    db.flush()
    return order


def create_order_from_checkout_webhook_payload(db: Session, payload: dict) -> Order | None:
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    razorpay_order_id = payment_entity.get("order_id")
    razorpay_payment_id = payment_entity.get("id")
    if not razorpay_order_id or not razorpay_payment_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment payload")

    intent = (
        db.query(CheckoutPaymentIntent)
        .filter(CheckoutPaymentIntent.razorpay_order_id == razorpay_order_id)
        .with_for_update()
        .first()
    )
    if intent is None:
        return None

    webhook_amount = payment_entity.get("amount")
    expected_amount = int(money(intent.total_amount) * 100)
    if webhook_amount is not None and int(webhook_amount) != expected_amount:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Captured payment amount does not match checkout")

    if intent.status in {CheckoutPaymentIntentStatus.EXPIRED, CheckoutPaymentIntentStatus.FAILED} and not intent.stock_reserved:
        refund_unfulfillable_checkout_payment(
            db,
            intent=intent,
            razorpay_payment_id=razorpay_payment_id,
            reason="Payment captured after inventory reservation was released",
        )
        return None

    return create_order_from_checkout_intent(
        db,
        intent=intent,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=intent.razorpay_signature,
    )

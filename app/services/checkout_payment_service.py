import json
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.orders import generate_order_number
from app.models.address import Address
from app.models.cart import CartItem
from app.models.checkout_payment_intent import (
    CheckoutPaymentIntent,
    CheckoutPaymentIntentStatus,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import ProductVariant
from app.core.pricing import money
from app.services.return_service import utc_now


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


def create_order_from_checkout_intent(
    db: Session,
    *,
    intent: CheckoutPaymentIntent,
    razorpay_payment_id: str,
    razorpay_signature: str | None,
) -> Order:
    existing_order = (
        db.query(Order)
        .filter(Order.idempotency_key == intent.razorpay_order_id)
        .first()
    )
    if existing_order is not None:
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
        sync_intent_success(
            intent,
            order_id=existing_order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        return existing_order

    snapshot_items = json.loads(intent.cart_snapshot or "[]")
    if not snapshot_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment cart snapshot is empty")

    address = _get_locked_address(db, intent)
    locked_variants = _lock_variants_for_snapshot(db, snapshot_items)

    order = Order(
        order_number=generate_order_number(db),
        user_id=intent.user_id,
        subtotal=money(intent.subtotal),
        tax_amount=money(intent.tax_amount),
        shipping_charge=money(0),
        discount_amount=money(0),
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

    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_payment_id == razorpay_payment_id)
        .with_for_update()
        .first()
    )
    if payment is None:
        payment = Payment(
            order_id=order.id,
            payment_method=PaymentMethod.RAZORPAY,
            payment_status=PaymentStatus.SUCCESS,
            amount=money(intent.total_amount),
            currency=intent.currency,
            razorpay_order_id=intent.razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            paid_at=utc_now(),
        )
        db.add(payment)
    else:
        payment.order_id = order.id
        payment.payment_status = PaymentStatus.SUCCESS
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

    return create_order_from_checkout_intent(
        db,
        intent=intent,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=intent.razorpay_signature,
    )

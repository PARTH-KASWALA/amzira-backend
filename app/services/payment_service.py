import hashlib
import hmac
import logging
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import razorpay
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order, OrderStatus
from app.models.product import ProductVariant
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.tasks.order_tasks import dispatch_fulfill_order
from app.tasks.email_tasks import send_order_confirmation

logger = logging.getLogger(__name__)
LOW_STOCK_WARNING_THRESHOLD = 5


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_paise(value) -> int:
    return int((_to_decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def get_razorpay_client() -> razorpay.Client:
    key_id = (settings.RAZORPAY_KEY_ID or "").strip()
    key_secret = (settings.RAZORPAY_KEY_SECRET or "").strip()

    if not key_id or not key_secret:
        raise HTTPException(status_code=500, detail="Razorpay credentials are not configured")

    logger.info(
        "razorpay_client_initialized key_id_prefix=%s key_id_suffix=%s secret_configured=%s",
        key_id[:8],
        key_id[-4:],
        True,
    )
    return razorpay.Client(auth=(key_id, key_secret))


def _log_stock_depletion_warning(variant: ProductVariant) -> None:
    if variant.stock_quantity <= LOW_STOCK_WARNING_THRESHOLD:
        logger.warning(
            "stock_depletion_warning variant_id=%s product_id=%s stock_quantity=%s",
            variant.id,
            variant.product_id,
            variant.stock_quantity,
        )
    if variant.stock_quantity <= 0:
        logger.warning(
            "stock_depleted variant_id=%s product_id=%s stock_quantity=%s",
            variant.id,
            variant.product_id,
            variant.stock_quantity,
        )


def create_razorpay_order(order: Order, db: Session) -> dict:
    """Create Razorpay order."""
    amount_paise = _to_paise(order.total_amount)
    razorpay_client = get_razorpay_client()

    razorpay_order = razorpay_client.order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": order.order_number,
            "notes": {
                "order_id": order.id,
                "customer_email": order.user.email,
            },
        }
    )

    payment = Payment(
        order_id=order.id,
        payment_method="razorpay",
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id=razorpay_order["id"],
    )
    db.add(payment)
    db.commit()

    return {
        "razorpay_order_id": razorpay_order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "order_number": order.order_number,
    }


def create_cod_payment(order: Order, db: Session) -> Payment:
    """
    Create Cash-on-Delivery payment.
    Stock is already reserved at order creation.
    This function MUST NOT touch inventory.
    """
    try:
        if order.stock_deducted:
            existing = db.query(Payment).filter(Payment.order_id == order.id).first()
            if existing:
                return existing

        locked_variants = {}
        if not order.stock_deducted:
            variant_ids = sorted({item.variant_id for item in order.items})
            locked_variants = {
                variant.id: variant
                for variant in (
                    db.query(ProductVariant)
                    .filter(ProductVariant.id.in_(variant_ids))
                    .with_for_update()
                    .all()
                )
            }

            for item in order.items:
                variant = locked_variants.get(item.variant_id)
                if not variant or variant.stock_quantity < item.quantity:
                    raise HTTPException(status_code=400, detail=f"Insufficient stock for {item.product_name}")

        payment = db.query(Payment).filter(Payment.order_id == order.id).first()
        if payment and payment.payment_status == PaymentStatus.SUCCESS:
            raise HTTPException(status_code=409, detail="Payment already processed")
        if not payment:
            payment = Payment(
                order_id=order.id,
                payment_method=PaymentMethod.COD,
                payment_status=PaymentStatus.PENDING,
                amount=order.total_amount,
                currency="INR",
            )
            db.add(payment)

        if not order.stock_deducted:
            for item in order.items:
                locked_variants[item.variant_id].stock_quantity -= item.quantity
                _log_stock_depletion_warning(locked_variants[item.variant_id])

        payment.payment_status = PaymentStatus.PENDING
        payment.paid_at = None
        order.status = OrderStatus.CONFIRMED
        order.expires_at = None
        order.stock_deducted = True
        db.commit()
        db.refresh(payment)
        _try_shiprocket_fulfillment(order, db)

        try:
            send_order_confirmation.apply_async(args=[order.id], ignore_result=True)
        except Exception as email_err:
            logger.error("COD confirmation email failed for order %s: %s", order.id, email_err)
        return payment
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("payment_failed order_id=%s error=%s", order.id, str(exc))
        logger.error("COD payment failed for order %s: %s", order.id, exc)
        raise HTTPException(
            status_code=400,
            detail="COD payment could not be completed",
        )


def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """Verify Razorpay payment signature."""
    key_secret = (settings.RAZORPAY_KEY_SECRET or "").strip()
    if not key_secret:
        raise HTTPException(status_code=500, detail="Razorpay secret is not configured")

    message = f"{razorpay_order_id}|{razorpay_payment_id}"

    generated_signature = hmac.new(
        key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(generated_signature, razorpay_signature)


def verify_razorpay_webhook_signature(body: bytes, signature: str) -> None:
    secret = (settings.RAZORPAY_WEBHOOK_SECRET or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="Razorpay webhook secret is not configured")

    generated_signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(generated_signature, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")


def _queue_order_confirmation(order_id: int) -> None:
    try:
        send_order_confirmation.apply_async(args=[order_id], ignore_result=True)
    except Exception:
        logger.exception("Failed to queue order confirmation email for order %s", order_id)


def _try_shiprocket_fulfillment(order: Order, db: Session) -> None:
    _ = db
    try:
        dispatch_fulfill_order(order.id)
    except Exception:
        logger.exception("shiprocket_fulfillment_queue_failed order_id=%s", order.id)


def _lock_variants_for_order(db: Session, order: Order) -> dict[int, ProductVariant]:
    variant_ids = sorted({item.variant_id for item in order.items})
    if not variant_ids:
        return {}
    return {
        variant.id: variant
        for variant in (
            db.query(ProductVariant)
            .filter(ProductVariant.id.in_(variant_ids))
            .with_for_update()
            .all()
        )
    }


def _mark_payment_success(
    payment: Payment,
    razorpay_payment_id: str,
    razorpay_signature: str | None,
    gateway_response: dict | None,
    db: Session,
) -> Order:
    if payment.payment_status == PaymentStatus.SUCCESS:
        return payment.order
    if payment.payment_status == PaymentStatus.FAILED:
        raise HTTPException(status_code=409, detail="Payment already marked failed")

    order = payment.order
    locked_variants = {}
    if not order.stock_deducted:
        locked_variants = _lock_variants_for_order(db, order)
        for item in order.items:
            variant = locked_variants.get(item.variant_id)
            if not variant or variant.stock_quantity < item.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for {item.product_name}")

    payment.razorpay_payment_id = razorpay_payment_id
    if razorpay_signature:
        payment.razorpay_signature = razorpay_signature
    if gateway_response is not None:
        payment.gateway_response = json.dumps(gateway_response)
    payment.payment_status = PaymentStatus.SUCCESS
    payment.paid_at = datetime.utcnow()

    if not order.stock_deducted:
        for item in order.items:
            locked_variants[item.variant_id].stock_quantity -= item.quantity
            _log_stock_depletion_warning(locked_variants[item.variant_id])

    order.status = OrderStatus.CONFIRMED
    order.expires_at = None
    order.stock_deducted = True
    db.flush()
    return order


def _mark_payment_failed(payment: Payment, gateway_response: dict | None = None) -> Order:
    payment.payment_status = PaymentStatus.FAILED
    if gateway_response is not None:
        payment.gateway_response = json.dumps(gateway_response)
    payment.order.status = OrderStatus.PLACED
    return payment.order


def process_verified_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: Session,
) -> Order:
    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_order_id == razorpay_order_id)
        .with_for_update()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")

    if not verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        _mark_payment_failed(payment)
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    try:
        order = _mark_payment_success(
            payment=payment,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            gateway_response=None,
            db=db,
        )
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        raise

    _try_shiprocket_fulfillment(order, db)
    _queue_order_confirmation(order.id)
    return order


def process_captured_payment_from_webhook(payload: dict, db: Session) -> Order | None:
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    razorpay_order_id = payment_entity.get("order_id")
    razorpay_payment_id = payment_entity.get("id")
    if not razorpay_order_id or not razorpay_payment_id:
        raise HTTPException(status_code=400, detail="Invalid payment payload")

    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_order_id == razorpay_order_id)
        .with_for_update()
        .first()
    )
    if payment is None:
        return None

    was_success = payment.payment_status == PaymentStatus.SUCCESS
    try:
        order = _mark_payment_success(
            payment=payment,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=payment.razorpay_signature,
            gateway_response=payload,
            db=db,
        )
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        raise

    _try_shiprocket_fulfillment(order, db)
    if not was_success:
        _queue_order_confirmation(order.id)
    return order


def cancel_payment_from_webhook(payload: dict, db: Session) -> Order | None:
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    razorpay_order_id = payment_entity.get("order_id")
    if not razorpay_order_id:
        raise HTTPException(status_code=400, detail="Invalid payment payload")

    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_order_id == razorpay_order_id)
        .with_for_update()
        .first()
    )
    if payment is None:
        return None

    _mark_payment_failed(payment, gateway_response=payload)
    db.commit()
    return payment.order


def process_successful_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: Session,
) -> Order:
    """Process successful payment."""
    order = process_verified_payment(
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=razorpay_signature,
        db=db,
    )
    return order

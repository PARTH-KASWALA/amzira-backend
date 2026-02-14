import hashlib
import hmac
import logging
from datetime import datetime

import razorpay
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order, OrderStatus
from app.models.product import ProductVariant
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.tasks.email_tasks import send_order_confirmation

razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
logger = logging.getLogger(__name__)
LOW_STOCK_WARNING_THRESHOLD = 5


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
    amount_paise = int(order.total_amount * 100)

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

        try:
            send_order_confirmation.delay(order.id)
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
    message = f"{razorpay_order_id}|{razorpay_payment_id}"

    generated_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(generated_signature, razorpay_signature)


def process_successful_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: Session,
) -> Order:
    """Process successful payment."""
    payment = db.query(Payment).filter(Payment.razorpay_order_id == razorpay_order_id).first()

    if not payment:
        raise ValueError("Payment record not found")

    if not verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        payment.payment_status = PaymentStatus.FAILED
        db.commit()
        raise ValueError("Invalid payment signature")

    try:
        if payment.payment_status == PaymentStatus.SUCCESS:
            raise ValueError("Payment already processed")

        order = payment.order
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
                    raise ValueError(f"Insufficient stock for {item.product_name}")

        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = razorpay_signature
        payment.payment_status = PaymentStatus.SUCCESS
        payment.paid_at = datetime.utcnow()

        if not order.stock_deducted:
            for item in order.items:
                locked_variants[item.variant_id].stock_quantity -= item.quantity
                _log_stock_depletion_warning(locked_variants[item.variant_id])
        order.status = OrderStatus.CONFIRMED
        order.expires_at = None
        order.stock_deducted = True

        db.commit()
        db.refresh(order)

        try:
            send_order_confirmation.delay(order.id)
        except Exception:
            logger.exception(
                "Failed to queue order confirmation email for order %s",
                getattr(order, "id", None),
            )

        return order
    except Exception:
        raise

from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
import razorpay
from pydantic import BaseModel
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus, PaymentMethod
from app.core.config import settings
from app.core.rate_limiter import limiter
from app.models.product import ProductVariant
from app.services.payment_service import (
    cancel_payment_from_webhook,
    process_captured_payment_from_webhook,
    process_verified_payment,
    verify_razorpay_webhook_signature,
)
from app.utils.response import success
import structlog

router = APIRouter()

logger = structlog.get_logger()
LOW_STOCK_WARNING_THRESHOLD = 5


def _to_paise(value) -> int:
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


class CreatePaymentOrderRequest(BaseModel):
    order_id: int


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def _payment_error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            "errors": [{"code": code}],
        },
    )


def _cancel_order_and_restore_stock(order: Order) -> None:
    """Cancel a reserved order and restore stock once."""
    if order.status == OrderStatus.CANCELLED:
        return

    if order.stock_deducted and order.status in {OrderStatus.PLACED, OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}:
        for item in order.items:
            variant = item.variant
            variant.stock_quantity += item.quantity
        order.stock_deducted = False

    order.status = OrderStatus.CANCELLED
    order.expires_at = None


def _log_stock_depletion_warning(variant: ProductVariant) -> None:
    if variant.stock_quantity <= LOW_STOCK_WARNING_THRESHOLD:
        logger.warning(
            "stock_depletion_warning",
            variant_id=variant.id,
            product_id=variant.product_id,
            stock_quantity=variant.stock_quantity,
        )
    if variant.stock_quantity <= 0:
        logger.warning(
            "stock_depleted",
            variant_id=variant.id,
            product_id=variant.product_id,
            stock_quantity=variant.stock_quantity,
        )


@router.post(
    "/create-order",
    summary="Create Razorpay payment order",
    description="""
Creates a gateway order for an existing user order.

Process:
1. Validates order ownership
2. Creates Razorpay order in paise
3. Persists payment intent record
4. Returns gateway payload required by frontend checkout
""",
    responses={
        200: {"description": "Payment order created successfully"},
        401: {"description": "Authentication required"},
        404: {"description": "Order not found"},
    },
    tags=["Payments"],
)
@limiter.limit("20/minute")
def create_payment_order(
    request: Request,
    payload: CreatePaymentOrderRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Use /create-payment-order commerce checkout endpoint instead.",
    )


@router.post("/verify")
@limiter.limit("30/minute")
def verify_payment(
    request: Request,
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Use /verify-payment commerce checkout endpoint instead.",
    )


@router.post("/webhook")
@limiter.limit("120/minute")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Razorpay webhooks"""
    payload = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    event = await request.json()
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    logger.info(
        "webhook_received",
        webhook_event=event.get("event"),
        payment_id=payment_entity.get("id"),
        order_id=payment_entity.get("order_id"),
        amount=payment_entity.get("amount"),
    )
    
    # Verify webhook signature
    try:
        verify_razorpay_webhook_signature(payload, signature or "")
    except HTTPException:
        logger.warning(
            "webhook_signature_invalid",
            webhook_event=event.get("event"),
            payment_id=payment_entity.get("id"),
            order_id=payment_entity.get("order_id"),
            amount=payment_entity.get("amount"),
        )
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    if event["event"] == "payment.captured":
        existing_payment = (
            db.query(Payment)
            .filter(Payment.razorpay_order_id == payment_entity.get("order_id"))
            .first()
        )
        if existing_payment and existing_payment.payment_status == PaymentStatus.SUCCESS:
            return success(data={"status": "duplicate"}, message="Payment already processed")

        try:
            order = process_captured_payment_from_webhook(payload=event, db=db)
            if order is None:
                return success(data={"status": "ok"}, message="Webhook processed")
            logger.info(
                "webhook_payment_success",
                payment_id=payment_entity.get("id"),
                order_id=order.id,
                amount=payment_entity.get("amount"),
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("webhook_payment_processing_failed", payment_id=payment_entity.get("id"))
            raise HTTPException(status_code=500, detail="Webhook payment processing failed")
    
    elif event["event"] == "payment.failed":
        order = cancel_payment_from_webhook(payload=event, db=db)
        if order is not None:
            logger.error(
                "webhook_payment_failed",
                order_id=order.id,
                gateway_order_id=payment_entity.get("order_id"),
                amount=payment_entity.get("amount"),
            )
    
    return success(data={"status": "ok"}, message="Webhook processed")

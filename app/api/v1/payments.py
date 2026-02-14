from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
import razorpay
import hmac
import hashlib
from datetime import datetime
from pydantic import BaseModel
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus, PaymentMethod
from app.core.config import settings
from app.core.rate_limiter import limiter
from app.models.product import ProductVariant
from app.tasks.email_tasks import send_order_confirmation
from app.utils.response import success
import structlog

router = APIRouter()

logger = structlog.get_logger()
LOW_STOCK_WARNING_THRESHOLD = 5

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

    if order.stock_deducted and order.status in {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}:
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
    """Create Razorpay order for payment"""
    order_id = payload.order_id
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=400, detail="Payment can only be created for pending orders")
    
    # Amount in paise (multiply by 100)
    amount_paise = int(order.total_amount * 100)
    
    # Create Razorpay order
    razorpay_order = razorpay_client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": order.order_number,
        "notes": {
            "order_id": order.id,
            "customer_email": current_user.email
        }
    })
    
    # Save payment record
    payment = db.query(Payment).filter(Payment.order_id == order.id).first()
    if payment and payment.payment_status == PaymentStatus.SUCCESS:
        raise HTTPException(status_code=409, detail="Payment already processed")
    if not payment:
        payment = Payment(
            order_id=order.id,
            payment_method=PaymentMethod.RAZORPAY,
            amount=order.total_amount,
            currency="INR",
            razorpay_order_id=razorpay_order["id"],
            payment_status=PaymentStatus.PENDING,
        )
        db.add(payment)
    else:
        payment.razorpay_order_id = razorpay_order["id"]
        payment.payment_status = PaymentStatus.PENDING
    db.commit()
    
    return success(
        data={
            "razorpay_order_id": razorpay_order["id"],
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount": amount_paise,
            "currency": "INR",
            "order_number": order.order_number,
        },
        message="Payment order created",
    )


@router.post("/verify")
@limiter.limit("30/minute")
def verify_payment(
    request: Request,
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Verify Razorpay payment signature"""
    razorpay_order_id = payload.razorpay_order_id
    razorpay_payment_id = payload.razorpay_payment_id
    razorpay_signature = payload.razorpay_signature
    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        raise _payment_error("PAYMENT_CANCELLED", "Payment was cancelled by user", 400)

    # Find payment record
    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_order_id == razorpay_order_id)
        .with_for_update()
        .first()
    )
    
    if not payment:
        raise _payment_error("PAYMENT_CANCELLED", "Payment record not found", 404)
    
    # Verify order belongs to user
    if payment.order.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Verify signature
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    generated_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(generated_signature, razorpay_signature):
        # Log security event for failed signature verification
        try:
            logger.warning(
                "payment_signature_mismatch",
                order_id=razorpay_order_id,
                payment_id=razorpay_payment_id,
                user_id=current_user.id,
            )
        except Exception:
            # Ensure logging failures do not block flow
            pass

        try:
            payment.payment_status = PaymentStatus.FAILED
            payment.order.status = OrderStatus.PENDING
            db.commit()
            logger.error(
                "payment_failed_signature",
                payment_id=payment.id,
                order_id=payment.order_id,
                amount=payment.amount,
            )
            logger.error(
                "payment_failed",
                payment_id=payment.id,
                order_id=payment.order_id,
                amount=payment.amount,
            )
        except Exception:
            db.rollback()
            logger.exception("payment_failure_commit_error", order_id=payment.order_id)
        raise _payment_error("PAYMENT_VERIFICATION_FAILED", "Invalid payment signature", 400)

    if payment.payment_status == PaymentStatus.SUCCESS:
        raise _payment_error("PAYMENT_FAILED", "Payment already processed", 409)
    if payment.payment_status == PaymentStatus.FAILED:
        raise _payment_error("PAYMENT_FAILED", "Payment failed. Please retry.", 400)

    try:
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
                    raise _payment_error(
                        "PAYMENT_FAILED",
                        f"Insufficient stock for {item.product_name}",
                        400,
                    )

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
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("payment_verification_atomic_failure", payment_id=payment.id, order_id=payment.order_id)
        raise _payment_error("PAYMENT_FAILED", "Payment processing failed", 500)

    try:
        send_order_confirmation.delay(order.id)
    except Exception:
        logger.exception(
            "order_confirmation_queue_failed",
            order_id=getattr(order, "id", None),
        )
    
    return success(
        data={
            "order_number": order.order_number,
            "payment_status": payment.payment_status.value,
            "order_status": order.status.value,
        },
        message="Payment successful",
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
        razorpay_client.utility.verify_webhook_signature(
            payload.decode(),
            signature,
            settings.RAZORPAY_WEBHOOK_SECRET
        )
    except Exception:
        logger.warning(
            "webhook_signature_invalid",
            webhook_event=event.get("event"),
            payment_id=payment_entity.get("id"),
            order_id=payment_entity.get("order_id"),
            amount=payment_entity.get("amount"),
        )
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    if event["event"] == "payment.captured":
        # Payment successful
        payment_entity = event["payload"]["payment"]["entity"]
        razorpay_payment_id = payment_entity["id"]
        razorpay_order_id = payment_entity["order_id"]
        
        # Update payment
        payment = db.query(Payment).filter(
            Payment.razorpay_order_id == razorpay_order_id
        ).first()
        
        if payment:
            if payment.payment_status == PaymentStatus.SUCCESS:
                return success(data={"status": "duplicate"}, message="Payment already processed")

            try:
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
                            raise HTTPException(status_code=400, detail=f"Insufficient stock for {item.product_name}")

                payment.razorpay_payment_id = razorpay_payment_id
                payment.payment_status = PaymentStatus.SUCCESS
                payment.paid_at = datetime.utcnow()
                if not order.stock_deducted:
                    for item in order.items:
                        locked_variants[item.variant_id].stock_quantity -= item.quantity
                order.status = OrderStatus.CONFIRMED
                order.expires_at = None
                order.stock_deducted = True
                db.commit()
                logger.info(
                    "webhook_payment_success",
                    payment_id=razorpay_payment_id,
                    order_id=order.id,
                    amount=payment.amount,
                )
            except HTTPException:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                logger.exception("webhook_payment_processing_failed", payment_id=razorpay_payment_id)
                raise HTTPException(status_code=500, detail="Webhook payment processing failed")

            try:
                send_order_confirmation.delay(order.id)
            except Exception:
                logger.exception(
                    "order_confirmation_queue_failed_webhook",
                    order_id=getattr(order, "id", None),
                )
    
    elif event["event"] == "payment.failed":
        # Payment failed
        payment_entity = event["payload"]["payment"]["entity"]
        razorpay_order_id = payment_entity["order_id"]
        
        payment = db.query(Payment).filter(
            Payment.razorpay_order_id == razorpay_order_id
        ).first()
        
        if payment:
            payment.payment_status = PaymentStatus.FAILED
            # Keep order pending so frontend can distinguish retryable failures from abandonment.
            payment.order.status = OrderStatus.PENDING
            db.commit()
            logger.error(
                "webhook_payment_failed",
                payment_id=payment.id,
                order_id=payment.order_id,
                amount=payment.amount,
            )
            logger.error(
                "payment_failed",
                payment_id=payment.id,
                order_id=payment.order_id,
                amount=payment.amount,
            )
    
    return success(data={"status": "ok"}, message="Webhook processed")

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
import razorpay
import hmac
import hashlib
from datetime import datetime
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus, PaymentMethod
from app.core.config import settings
import structlog

router = APIRouter()

logger = structlog.get_logger()

# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


@router.post("/create-order")
def create_payment_order(
    order_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create Razorpay order for payment"""
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
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
    payment = Payment(
        order_id=order.id,
        payment_method=PaymentMethod.RAZORPAY,
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id=razorpay_order["id"]
    )
    db.add(payment)
    db.commit()
    
    return {
        "razorpay_order_id": razorpay_order["id"],
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "amount": amount_paise,
        "currency": "INR",
        "order_number": order.order_number
    }


@router.post("/verify")
def verify_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Verify Razorpay payment signature"""
    # Find payment record
    payment = db.query(Payment).filter(
        Payment.razorpay_order_id == razorpay_order_id
    ).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    
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

        payment.payment_status = PaymentStatus.FAILED
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    
    # Update payment
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.payment_status = PaymentStatus.SUCCESS
    payment.paid_at = datetime.utcnow()
    
    # Update order status
    order = payment.order
    order.status = OrderStatus.CONFIRMED
    
    db.commit()
    
    # TODO: Send confirmation email
    
    return {
        "success": True,
        "order_number": order.order_number,
        "message": "Payment successful"
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Razorpay webhooks"""
    payload = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    
    # Verify webhook signature
    try:
        razorpay_client.utility.verify_webhook_signature(
            payload.decode(),
            signature,
            settings.RAZORPAY_WEBHOOK_SECRET
        )
    except:
        # Log webhook verification failure
        try:
            logger.warning("webhook_signature_invalid")
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    # Process webhook event
    event = await request.json()
    
    if event["event"] == "payment.captured":
        # Payment successful
        payment_entity = event["payload"]["payment"]["entity"]
        razorpay_payment_id = payment_entity["id"]
        razorpay_order_id = payment_entity["order_id"]
        
        # Update payment
        payment = db.query(Payment).filter(
            Payment.razorpay_order_id == razorpay_order_id
        ).first()
        
        if payment and payment.payment_status == PaymentStatus.PENDING:
            payment.razorpay_payment_id = razorpay_payment_id
            payment.payment_status = PaymentStatus.SUCCESS
            payment.paid_at = datetime.utcnow()
            
            # Update order
            order = payment.order
            order.status = OrderStatus.CONFIRMED
            
            db.commit()
    
    elif event["event"] == "payment.failed":
        # Payment failed
        payment_entity = event["payload"]["payment"]["entity"]
        razorpay_order_id = payment_entity["order_id"]
        
        payment = db.query(Payment).filter(
            Payment.razorpay_order_id == razorpay_order_id
        ).first()
        
        if payment:
            payment.payment_status = PaymentStatus.FAILED
            db.commit()
    
    return {"status": "ok"}
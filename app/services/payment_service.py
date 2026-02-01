import razorpay
import hmac
import hashlib
from app.core.config import settings
from app.models.payment import Payment, PaymentStatus
from app.models.order import Order, OrderStatus
from app.models.product import ProductVariant
from app.utils.email import send_order_confirmation_email
import logging
from sqlalchemy.orm import Session

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

def create_razorpay_order(order: Order, db: Session) -> dict:
    """Create Razorpay order"""
    # Amount must be in paise (multiply by 100)
    amount_paise = int(order.total_amount * 100)
    
    razorpay_order = razorpay_client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": order.order_number,
        "notes": {
            "order_id": order.id,
            "customer_email": order.user.email
        }
    })
    
    # Save to payment table
    payment = Payment(
        order_id=order.id,
        payment_method="razorpay",
        amount=order.total_amount,
        currency="INR",
        razorpay_order_id=razorpay_order["id"]
    )
    db.add(payment)
    db.commit()
    
    return {
        "razorpay_order_id": razorpay_order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "order_number": order.order_number
    }

def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str
) -> bool:
    """Verify Razorpay payment signature (critical security step)"""
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    
    generated_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(generated_signature, razorpay_signature)

def process_successful_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: Session
) -> Order:
    """Process successful payment"""
    # Find payment record
    payment = db.query(Payment).filter(
        Payment.razorpay_order_id == razorpay_order_id
    ).first()
    
    if not payment:
        raise ValueError("Payment record not found")
    
    # Verify signature
    if not verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        payment.payment_status = PaymentStatus.FAILED
        db.commit()
        raise ValueError("Invalid payment signature")
    
    # Begin critical section: lock variants and deduct stock
    try:
        for item in payment.order.items:
            # Lock the variant row for update to prevent races
            variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).with_for_update().first()
            if not variant:
                payment.payment_status = PaymentStatus.FAILED
                db.commit()
                raise ValueError(f"Variant not found: {item.variant_id}")

            if variant.stock_quantity < item.quantity:
                payment.payment_status = PaymentStatus.FAILED
                db.commit()
                raise ValueError("Insufficient stock for variant")

            variant.stock_quantity -= item.quantity

        # Update payment and order after successful stock deduction
        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = razorpay_signature
        payment.payment_status = PaymentStatus.SUCCESS
        payment.paid_at = datetime.utcnow()

        order = payment.order
        order.status = OrderStatus.CONFIRMED

        db.commit()
        db.refresh(order)

        # Send confirmation email asynchronously. Failures are logged inside utility.
        try:
            send_order_confirmation_email(order)
        except Exception:
            logging.exception("Failed to queue order confirmation email for order %s", getattr(order, 'id', None))

        return order
    except Exception:
        # Re-raise to let caller handle payment failure semantics
        raise
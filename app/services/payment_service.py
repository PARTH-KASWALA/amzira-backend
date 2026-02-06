# import razorpay
# import hmac
# import hashlib
# from app.core.config import settings
# from app.models.payment import Payment, PaymentStatus, PaymentMethod
# from app.models.order import Order, OrderStatus
# from app.models.product import ProductVariant
# from app.utils.email import send_order_confirmation_email
# import logging
# from datetime import datetime
# from sqlalchemy.orm import Session

# # Initialize Razorpay client
# razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

# def create_razorpay_order(order: Order, db: Session) -> dict:
#     """Create Razorpay order"""
#     # Amount must be in paise (multiply by 100)
#     amount_paise = int(order.total_amount * 100)
    
#     razorpay_order = razorpay_client.order.create({
#         "amount": amount_paise,
#         "currency": "INR",
#         "receipt": order.order_number,
#         "notes": {
#             "order_id": order.id,
#             "customer_email": order.user.email
#         }
#     })
    
#     # Save to payment table
#     payment = Payment(
#         order_id=order.id,
#         payment_method="razorpay",
#         amount=order.total_amount,
#         currency="INR",
#         razorpay_order_id=razorpay_order["id"]
#     )
#     db.add(payment)
#     db.commit()
    
#     return {
#         "razorpay_order_id": razorpay_order["id"],
#         "amount": amount_paise,
#         "currency": "INR",
#         "order_number": order.order_number
#     }


# def create_cod_payment(order: Order, db: Session) -> Payment:
#     """Create COD payment record"""
#     payment = Payment(
#         order_id=order.id,
#         payment_method=PaymentMethod.COD,
#         payment_status=PaymentStatus.PENDING,  # Will be SUCCESS on delivery
#         amount=order.total_amount,
#         currency="INR"
#     )

#     db.add(payment)

#     # For COD, immediately confirm order but payment stays pending
#     order.status = OrderStatus.CONFIRMED

#     db.commit()
#     db.refresh(payment)

#     return payment

# def verify_payment_signature(
#     razorpay_order_id: str,
#     razorpay_payment_id: str,
#     razorpay_signature: str
# ) -> bool:
#     """Verify Razorpay payment signature (critical security step)"""
#     message = f"{razorpay_order_id}|{razorpay_payment_id}"
    
#     generated_signature = hmac.new(
#         settings.RAZORPAY_KEY_SECRET.encode(),
#         message.encode(),
#         hashlib.sha256
#     ).hexdigest()
    
#     return hmac.compare_digest(generated_signature, razorpay_signature)



# def process_successful_payment(
#     razorpay_order_id: str,
#     razorpay_payment_id: str,
#     razorpay_signature: str,
#     db: Session
# ) -> Order:
#     """Process successful payment"""
#     # Find payment record
#     payment = db.query(Payment).filter(
#         Payment.razorpay_order_id == razorpay_order_id
#     ).first()
    
#     if not payment:
#         raise ValueError("Payment record not found")
    
#     # Verify signature
#     if not verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
#         payment.payment_status = PaymentStatus.FAILED
#         db.commit()
#         raise ValueError("Invalid payment signature")
    
#     # Begin critical section: lock variants and deduct stock
#     try:
#         # First, lock and validate all variants to ensure sufficient stock
#         for item in payment.order.items:
#             variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).with_for_update().first()
#             if not variant:
#                 payment.payment_status = PaymentStatus.FAILED
#                 db.commit()
#                 raise ValueError(f"Variant not found: {item.variant_id}")

#             if variant.stock_quantity < item.quantity:
#                 payment.payment_status = PaymentStatus.FAILED
#                 db.commit()
#                 raise ValueError("Insufficient stock for variant")

#         # All checks passed: deduct stock
#         for item in payment.order.items:
#             variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).with_for_update().first()
#             # variant should exist and have sufficient stock from previous loop
#             variant.stock_quantity -= item.quantity

#         # Update payment and order after successful stock deduction
#         payment.razorpay_payment_id = razorpay_payment_id
#         payment.razorpay_signature = razorpay_signature
#         payment.payment_status = PaymentStatus.SUCCESS
#         payment.paid_at = datetime.utcnow()

#         order = payment.order
#         order.status = OrderStatus.CONFIRMED

#         db.commit()
#         db.refresh(order)

#         # Send confirmation email asynchronously. Failures are logged inside utility.
#         try:
#             send_order_confirmation_email(order)
#         except Exception:
#             logging.exception("Failed to queue order confirmation email for order %s", getattr(order, 'id', None))

#         return order
#     except Exception:
#         # Re-raise to let caller handle payment failure semantics
#         raise




import razorpay
import hmac
import hashlib
from fastapi import HTTPException
from app.core.config import settings
from app.models.payment import Payment, PaymentStatus, PaymentMethod
from app.models.order import Order, OrderStatus
from app.tasks.email_tasks import send_order_confirmation
import logging
from datetime import datetime
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


# def create_cod_payment(order: Order, db: Session) -> Payment:
#     """Create COD payment record and deduct stock"""
#     payment = Payment(
#         order_id=order.id,
#         payment_method=PaymentMethod.COD,
#         payment_status=PaymentStatus.PENDING,  # Will be SUCCESS on delivery
#         amount=order.total_amount,
#         currency="INR"
#     )

#     db.add(payment)

#     # CRITICAL: Deduct stock with row locking (same as Razorpay)
#     try:
#         # First, lock and validate all variants to ensure sufficient stock
#         for item in order.items:
#             variant = db.query(ProductVariant).filter(
#                 ProductVariant.id == item.variant_id
#             ).with_for_update().first()
            
#             if not variant:
#                 order.status = OrderStatus.CANCELLED
#                 payment.payment_status = PaymentStatus.FAILED
#                 db.commit()
#                 raise ValueError(f"Variant not found: {item.variant_id}")
            
#             if variant.stock_quantity < item.quantity:
#                 order.status = OrderStatus.CANCELLED
#                 payment.payment_status = PaymentStatus.FAILED
#                 db.commit()
#                 raise ValueError(f"Insufficient stock for {item.product_name} (variant {variant.id}). Available: {variant.stock_quantity}, Requested: {item.quantity}")
        
#         # All checks passed: deduct stock
#         for item in order.items:
#             variant = db.query(ProductVariant).filter(
#                 ProductVariant.id == item.variant_id
#             ).with_for_update().first()
#             # variant should exist and have sufficient stock from previous loop
#             variant.stock_quantity -= item.quantity
        
#         # For COD, confirm order after successful stock deduction
#         order.status = OrderStatus.CONFIRMED
        
#         db.commit()
#         db.refresh(payment)
        
#         # Send confirmation email asynchronously
#         try:
#             send_order_confirmation_email(order)
#         except Exception:
#             logging.exception("Failed to queue order confirmation email for order %s", getattr(order, 'id', None))
        
#         return payment
        
#     except Exception:
#         # Re-raise to let caller handle the error
#         raise


def create_cod_payment(order: Order, db: Session) -> Payment:
    """
    Create Cash-on-Delivery payment.
    Stock is already reserved at order creation.
    This function MUST NOT touch inventory.
    """
    try:
        payment = Payment(
            order_id=order.id,
            payment_method=PaymentMethod.COD,
            payment_status=PaymentStatus.PENDING,
            amount=order.total_amount,
            currency="INR",
        )
        db.add(payment)
        order.status = OrderStatus.CONFIRMED
        db.commit()
        db.refresh(payment)

        try:
            send_order_confirmation.delay(order.id)
        except Exception as email_err:
            logging.error(
                "COD confirmation email failed for order %s: %s",
                order.id,
                email_err,
            )
        return payment

    except Exception as exc:
        db.rollback()
        logging.error("COD payment failed for order %s: %s", order.id, exc)
        raise HTTPException(
            status_code=400,
            detail="COD payment could not be completed",
        )


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
    
    # Stock is reserved at order creation time.
    try:
        # Update payment and order after successful payment verification.
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

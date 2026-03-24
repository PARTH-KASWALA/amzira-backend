from datetime import datetime
import enum

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class CheckoutPaymentIntentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


class CheckoutPaymentIntent(Base):
    __tablename__ = "checkout_payment_intents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False)
    created_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    razorpay_order_id = Column(String(100), nullable=False, unique=True, index=True)
    razorpay_payment_id = Column(String(100), nullable=True, index=True)
    razorpay_signature = Column(String(200), nullable=True)

    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="INR", nullable=False)
    subtotal = Column(Float, nullable=False)
    tax_amount = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(
        Enum(CheckoutPaymentIntentStatus),
        default=CheckoutPaymentIntentStatus.PENDING,
        nullable=False,
        index=True,
    )

    cart_snapshot = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User")
    address = relationship("Address")
    created_order = relationship("Order", foreign_keys=[created_order_id])

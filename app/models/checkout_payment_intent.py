from datetime import datetime
import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class CheckoutPaymentIntentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


class CheckoutPaymentIntent(Base):
    __tablename__ = "checkout_payment_intents"
    __table_args__ = (Index("ix_checkout_payment_intents_razorpay_order_id", "razorpay_order_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False)
    created_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    razorpay_order_id = Column(String(100), nullable=False, unique=True)
    razorpay_payment_id = Column(String(100), nullable=True, index=True)
    razorpay_signature = Column(String(200), nullable=True)

    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="INR", nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_amount = Column(Numeric(10, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(10, 2), nullable=False)
    discount_amount = Column(Numeric(10, 2), default=0, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True, index=True)
    coupon_code = Column(String(50), nullable=True)
    coupon_reserved = Column(Boolean, default=False, nullable=False)
    status = Column(
        Enum(CheckoutPaymentIntentStatus),
        default=CheckoutPaymentIntentStatus.PENDING,
        nullable=False,
        index=True,
    )

    cart_snapshot = Column(Text, nullable=False)
    cart_fingerprint = Column(String(64), nullable=True, index=True)
    stock_reserved = Column(Boolean, default=False, nullable=False)
    reservation_released_at = Column(DateTime, nullable=True)
    reservation_consumed_at = Column(DateTime, nullable=True)
    failure_reason = Column(String(255), nullable=True)
    recovery_refund_id = Column(String(100), nullable=True, unique=True)
    recovery_refund_status = Column(String(30), nullable=True)
    recovery_gateway_response = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User")
    address = relationship("Address")
    created_order = relationship("Order", foreign_keys=[created_order_id])

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Enum, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.base_class import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    RAZORPAY = "razorpay"
    COD = "cod"  # Cash on Delivery


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    payment_status = Column(Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="INR", nullable=False)
    
    # Gateway-specific fields
    razorpay_order_id = Column(String(100), nullable=True, index=True)
    razorpay_payment_id = Column(String(100), nullable=True, index=True)
    razorpay_signature = Column(String(200), nullable=True)
    
    transaction_id = Column(String(100), nullable=True, index=True)
    gateway_response = Column(Text, nullable=True)  # Store JSON response
    
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="payment")
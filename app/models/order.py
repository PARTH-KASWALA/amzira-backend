from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Enum, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.base_class import Base


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Pricing
    subtotal = Column(Float, nullable=False)
    tax_amount = Column(Float, default=0.0)
    shipping_charge = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    coupon_code = Column(String(50), nullable=True)
    total_amount = Column(Float, nullable=False)
    
    # Status & Tracking
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    tracking_number = Column(String(100), nullable=True)
    carrier_name = Column(String(100), nullable=True)  # e.g., "FedEx", "UPS", "India Post"
    estimated_delivery_date = Column(DateTime, nullable=True)
    
    # Address (store IDs for reference)
    shipping_address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False)
    billing_address_id = Column(Integer, ForeignKey("addresses.id"), nullable=False)
    
    # Notes
    customer_notes = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payment = relationship("Payment", back_populates="order", uselist=False)
    shipping_address = relationship("Address", foreign_keys=[shipping_address_id])
    billing_address = relationship("Address", foreign_keys=[billing_address_id])
    coupon_usages = relationship("CouponUsage", back_populates="order", cascade="all, delete-orphan")
    status_history = relationship("OrderStatusHistory", back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.created_at")
    returns = relationship(
        "ReturnRequest",
        back_populates="order",
        cascade="all, delete-orphan",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False)
    
    product_name = Column(String(200), nullable=False)  # Snapshot at order time
    variant_details = Column(String(100), nullable=False)  # "Size: L, Color: Gold"
    
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product")
    variant = relationship("ProductVariant")
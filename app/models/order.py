from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.base_class import Base


class OrderStatus(str, enum.Enum):
    PLACED = "placed"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_created_at", "user_id", "created_at"),
        Index("ix_orders_status_created_at", "status", "created_at"),
        Index("ix_orders_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Pricing
    subtotal = Column(Numeric(10, 2), nullable=False)
    tax_amount = Column(Numeric(10, 2), default=0.0)
    shipping_charge = Column(Numeric(10, 2), default=0.0)
    discount_amount = Column(Numeric(10, 2), default=0.0)
    coupon_code = Column(String(50), nullable=True)
    total_amount = Column(Numeric(10, 2), nullable=False)
    
    # Status & Tracking
    status = Column(Enum(OrderStatus), default=OrderStatus.PLACED, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    stock_deducted = Column(Boolean, default=False, nullable=False)
    idempotency_key = Column(String(64), unique=True, nullable=True, index=True)
    tracking_number = Column(String(100), nullable=True)
    carrier_name = Column(String(100), nullable=True)  # e.g., "FedEx", "UPS", "India Post"
    courier_name = Column(String(100), nullable=True)
    shiprocket_order_id = Column(String(100), nullable=True, index=True)
    shipment_id = Column(String(100), nullable=True, index=True)
    tracking_id = Column(String(100), nullable=True, index=True)
    awb_code = Column(String(100), nullable=True, index=True)
    tracking_url = Column(String(500), nullable=True)
    current_location = Column(String(255), nullable=True)
    shiprocket_last_status = Column(String(100), nullable=True)
    courier_status = Column(String(100), nullable=True)
    pickup_scheduled_at = Column(DateTime, nullable=True)
    estimated_delivery_date = Column(DateTime, nullable=True)
    delivery_date = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    return_deadline = Column(DateTime(timezone=True), nullable=True)
    return_status = Column(String(20), nullable=False, default="not_applicable", server_default="not_applicable")
    
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
    __table_args__ = (Index("ix_order_items_order_id", "order_id"),)

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False)
    
    product_name = Column(String(200), nullable=False)  # Snapshot at order time
    variant_details = Column(String(100), nullable=False)  # "Size: L, Color: Gold"
    
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product")
    variant = relationship("ProductVariant")

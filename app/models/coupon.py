from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Enum, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.base_class import Base


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    
    discount_type = Column(Enum(DiscountType), nullable=False)
    discount_value = Column(Numeric(10, 2), nullable=False)  # Percentage (0-100) or fixed amount
    
    min_order_value = Column(Numeric(10, 2), default=0.0, nullable=False)
    max_discount = Column(Numeric(10, 2), nullable=True)  # Max discount for percentage type
    
    usage_limit = Column(Integer, nullable=True)  # Global usage limit
    used_count = Column(Integer, default=0, nullable=False)
    reserved_count = Column(Integer, default=0, nullable=False)
    
    per_user_limit = Column(Integer, default=1, nullable=False)  # How many times per user
    
    is_active = Column(Boolean, default=True, nullable=False)
    expiry_date = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    usages = relationship("CouponUsage", back_populates="coupon", cascade="all, delete-orphan")

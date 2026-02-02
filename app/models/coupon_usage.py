from sqlalchemy import Column, Integer, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base


class CouponUsage(Base):
    __tablename__ = "coupon_usages"

    id = Column(Integer, primary_key=True, index=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    
    used_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    coupon = relationship("Coupon", back_populates="usages")
    user = relationship("User")
    order = relationship("Order")
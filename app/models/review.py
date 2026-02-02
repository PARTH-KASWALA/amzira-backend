from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.db.base_class import Base


class Review(Base):
    __tablename__ = "reviews"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    
    rating = Column(Integer, nullable=False)  # 1-5 stars
    comment = Column(Text, nullable=True)
    verified_purchase = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="reviews")
    product = relationship("Product", back_populates="reviews")

    # Ensure one review per user per product
    __table_args__ = (
        UniqueConstraint('user_id', 'product_id', name='unique_user_product_review'),
    )
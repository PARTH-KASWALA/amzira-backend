from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base


class Wishlist(Base):
    __tablename__ = "wishlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="wishlist")
    product = relationship("Product", back_populates="wishlist_items")

    # Ensure one product per user in wishlist
    __table_args__ = (
        UniqueConstraint('user_id', 'product_id', name='unique_user_product_wishlist'),
    )
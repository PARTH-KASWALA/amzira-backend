from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.db.base_class import Base


class Review(Base):
    __tablename__ = "reviews"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Direct AMZIRA reviews are attached to a user. Imported marketplace
    # reviews deliberately are not: their purchase happened elsewhere and
    # must never be presented as a verified AMZIRA order.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    
    rating = Column(Integer, nullable=False)  # 1-5 stars
    comment = Column(Text, nullable=True)
    verified_purchase = Column(Boolean, default=False, nullable=False)
    marketplace_verified_purchase = Column(Boolean, default=False, nullable=False)
    source = Column(String(32), default="amzira", nullable=False)
    source_review_id = Column(String(128), nullable=True)
    source_product_id = Column(String(128), nullable=True)
    source_listing_url = Column(String(500), nullable=True)
    reviewer_name = Column(String(120), nullable=True)
    is_published = Column(Boolean, default=True, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="reviews")
    product = relationship("Product", back_populates="reviews")
    media = relationship("ReviewMedia", back_populates="review", cascade="all, delete-orphan")

    # Ensure one review per user per product
    __table_args__ = (
        UniqueConstraint('user_id', 'product_id', name='unique_user_product_review'),
        UniqueConstraint('source', 'source_review_id', name='uq_reviews_source_review_id'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_reviews_rating_range'),
        Index('ix_reviews_product_id', 'product_id'),
    )


class ReviewMedia(Base):
    __tablename__ = "review_media"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(String(36), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False)
    media_url = Column(String(500), nullable=False)
    alt_text = Column(String(200), nullable=True)
    consent_reference = Column(String(250), nullable=False)
    display_order = Column(Integer, default=0, nullable=False)

    review = relationship("Review", back_populates="media")

    __table_args__ = (Index("ix_review_media_review_id", "review_id"),)

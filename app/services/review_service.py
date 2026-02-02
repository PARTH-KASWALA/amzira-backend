from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from fastapi import HTTPException, status
from typing import List, Optional
import math

from app.models.review import Review
from app.models.product import Product
from app.models.order import Order, OrderItem, OrderStatus
from app.models.user import User
from app.schemas.review import ReviewCreate, ReviewUpdate, ReviewResponse, ReviewListResponse
from app.utils.response import success, error


class ReviewService:
    
    @staticmethod
    def _check_verified_purchase(db: Session, user_id: int, product_id: int) -> bool:
        """Check if user has purchased the product in a completed order."""
        completed_statuses = [OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED]
        
        exists = db.query(OrderItem).join(Order).filter(
            and_(
                Order.user_id == user_id,
                OrderItem.product_id == product_id,
                Order.status.in_(completed_statuses)
            )
        ).first() is not None
        
        return exists
    
    @staticmethod
    def _recalculate_product_ratings(db: Session, product_id: int):
        """Recalculate avg_rating and review_count for a product."""
        result = db.query(
            func.avg(Review.rating).label('avg_rating'),
            func.count(Review.id).label('review_count')
        ).filter(Review.product_id == product_id).first()
        
        avg_rating = float(result.avg_rating) if result.avg_rating else 0.0
        review_count = result.review_count or 0
        
        db.query(Product).filter(Product.id == product_id).update({
            'avg_rating': avg_rating,
            'review_count': review_count
        })
    
    @staticmethod
    def create_review(db: Session, user_id: int, review_data: ReviewCreate) -> ReviewResponse:
        """Create a new review. Enforces verified purchase and one review per user per product."""
        # Check if review already exists
        existing = db.query(Review).filter(
            and_(Review.user_id == user_id, Review.product_id == review_data.product_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already reviewed this product"
            )
        
        # Check verified purchase
        if not ReviewService._check_verified_purchase(db, user_id, review_data.product_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only review products you have purchased"
            )
        
        # Create review
        review = Review(
            user_id=user_id,
            product_id=review_data.product_id,
            rating=review_data.rating,
            comment=review_data.comment,
            verified_purchase=True
        )
        
        db.add(review)
        db.commit()
        db.refresh(review)
        
        # Recalculate product ratings
        ReviewService._recalculate_product_ratings(db, review_data.product_id)
        
        # Get user name
        user = db.query(User).filter(User.id == user_id).first()
        
        return ReviewResponse(
            id=review.id,
            user_id=review.user_id,
            product_id=review.product_id,
            rating=review.rating,
            comment=review.comment,
            verified_purchase=review.verified_purchase,
            created_at=review.created_at,
            user_name=user.full_name if user else "Unknown"
        )
    
    @staticmethod
    def get_reviews_for_product(
        db: Session, 
        product_id: int, 
        page: int = 1, 
        per_page: int = 10
    ) -> ReviewListResponse:
        """Get paginated reviews for a product."""
        offset = (page - 1) * per_page
        
        query = db.query(Review, User.full_name.label('user_name')).join(User).filter(
            Review.product_id == product_id
        ).order_by(Review.created_at.desc())
        
        total = query.count()
        reviews = query.offset(offset).limit(per_page).all()
        
        review_responses = [
            ReviewResponse(
                id=r.Review.id,
                user_id=r.Review.user_id,
                product_id=r.Review.product_id,
                rating=r.Review.rating,
                comment=r.Review.comment,
                verified_purchase=r.Review.verified_purchase,
                created_at=r.Review.created_at,
                user_name=r.user_name
            ) for r in reviews
        ]
        
        return ReviewListResponse(
            reviews=review_responses,
            total=total,
            page=page,
            per_page=per_page
        )
    
    @staticmethod
    def update_review(
        db: Session, 
        review_id: str, 
        user_id: int, 
        user_role: str, 
        review_data: ReviewUpdate
    ) -> ReviewResponse:
        """Update a review. Only owner or admin can update."""
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Review not found"
            )
        
        # Check ownership or admin
        if review.user_id != user_id and user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only edit your own reviews"
            )
        
        # Update fields
        if review_data.rating is not None:
            review.rating = review_data.rating
        if review_data.comment is not None:
            review.comment = review_data.comment
        
        db.commit()
        db.refresh(review)
        
        # Recalculate product ratings
        ReviewService._recalculate_product_ratings(db, review.product_id)
        
        # Get user name
        user = db.query(User).filter(User.id == review.user_id).first()
        
        return ReviewResponse(
            id=review.id,
            user_id=review.user_id,
            product_id=review.product_id,
            rating=review.rating,
            comment=review.comment,
            verified_purchase=review.verified_purchase,
            created_at=review.created_at,
            user_name=user.full_name if user else "Unknown"
        )
    
    @staticmethod
    def delete_review(db: Session, review_id: str, user_id: int, user_role: str):
        """Delete a review. Only owner or admin can delete."""
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Review not found"
            )
        
        # Check ownership or admin
        if review.user_id != user_id and user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own reviews"
            )
        
        product_id = review.product_id
        db.delete(review)
        db.commit()
        
        # Recalculate product ratings
        ReviewService._recalculate_product_ratings(db, product_id)
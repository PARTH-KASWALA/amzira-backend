from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.api.deps import get_current_user
from app.core.rate_limiter import limiter
from app.models.user import User
from app.services.review_service import ReviewService
from app.schemas.review import ReviewCreate, ReviewUpdate, ReviewListResponse
from app.utils.response import success

router = APIRouter()


@router.post("/", response_model=dict)
@limiter.limit("10/hour")
def create_review(
    request: Request,
    review_data: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new review for a product. Requires verified purchase."""
    review = ReviewService.create_review(db, current_user.id, review_data)
    return success(data=review.dict(), message="Review created successfully")


@router.get("/product/{product_id}", response_model=dict)
@limiter.limit("100/minute")
def get_product_reviews(
    request: Request,
    product_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """Get paginated reviews for a product. Public endpoint."""
    result = ReviewService.get_reviews_for_product(db, product_id, page, per_page)
    return success(data=result.dict(), message="Reviews retrieved successfully")


@router.put("/{review_id}", response_model=dict)
@limiter.limit("20/minute")
def update_review(
    request: Request,
    review_id: str,
    review_data: ReviewUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a review. Only owner or admin can update."""
    review = ReviewService.update_review(db, review_id, current_user.id, current_user.role.value, review_data)
    return success(data=review.dict(), message="Review updated successfully")


@router.delete("/{review_id}", response_model=dict)
@limiter.limit("20/minute")
def delete_review(
    request: Request,
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a review. Only owner or admin can delete."""
    ReviewService.delete_review(db, review_id, current_user.id, current_user.role.value)
    return success(message="Review deleted successfully")

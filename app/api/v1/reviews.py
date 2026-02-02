from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.review_service import ReviewService
from app.schemas.review import ReviewCreate, ReviewUpdate, ReviewListResponse
from app.utils.response import success, error

router = APIRouter()


@router.post("/", response_model=dict)
def create_review(
    review_data: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new review for a product. Requires verified purchase."""
    try:
        review = ReviewService.create_review(db, current_user.id, review_data)
        return success(data=review.dict(), message="Review created successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to create review", errors={"detail": str(e)})


@router.get("/product/{product_id}", response_model=dict)
def get_product_reviews(
    product_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """Get paginated reviews for a product. Public endpoint."""
    try:
        result = ReviewService.get_reviews_for_product(db, product_id, page, per_page)
        return success(data=result.dict(), message="Reviews retrieved successfully")
    except Exception as e:
        return error(message="Failed to retrieve reviews", errors={"detail": str(e)})


@router.put("/{review_id}", response_model=dict)
def update_review(
    review_id: str,
    review_data: ReviewUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a review. Only owner or admin can update."""
    try:
        review = ReviewService.update_review(db, review_id, current_user.id, current_user.role.value, review_data)
        return success(data=review.dict(), message="Review updated successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to update review", errors={"detail": str(e)})


@router.delete("/{review_id}", response_model=dict)
def delete_review(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a review. Only owner or admin can delete."""
    try:
        ReviewService.delete_review(db, review_id, current_user.id, current_user.role.value)
        return success(message="Review deleted successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to delete review", errors={"detail": str(e)})
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.wishlist_service import WishlistService
from app.schemas.wishlist import WishlistCreate, WishlistListResponse
from app.utils.response import success, error

router = APIRouter()


@router.post("/", response_model=dict)
def add_to_wishlist(
    wishlist_data: WishlistCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a product to user's wishlist."""
    try:
        wishlist_item = WishlistService.add_to_wishlist(db, current_user.id, wishlist_data)
        return success(data=wishlist_item.dict(), message="Product added to wishlist")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to add to wishlist", errors={"detail": str(e)})


@router.delete("/{product_id}", response_model=dict)
def remove_from_wishlist(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove a product from user's wishlist."""
    try:
        WishlistService.remove_from_wishlist(db, current_user.id, product_id)
        return success(message="Product removed from wishlist")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to remove from wishlist", errors={"detail": str(e)})


@router.get("/", response_model=dict)
def get_user_wishlist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's wishlist with product details."""
    try:
        wishlist = WishlistService.get_user_wishlist(db, current_user.id)
        return success(data=wishlist.dict(), message="Wishlist retrieved successfully")
    except Exception as e:
        return error(message="Failed to retrieve wishlist", errors={"detail": str(e)})


@router.get("/check/{product_id}", response_model=dict)
def check_wishlist_status(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if a product is in user's wishlist."""
    try:
        in_wishlist = WishlistService.check_in_wishlist(db, current_user.id, product_id)
        return success(data={"in_wishlist": in_wishlist}, message="Wishlist status checked")
    except Exception as e:
        return error(message="Failed to check wishlist status", errors={"detail": str(e)})
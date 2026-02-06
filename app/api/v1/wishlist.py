from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.wishlist_service import WishlistService
from app.schemas.wishlist import WishlistCreate, WishlistListResponse
from app.utils.response import success

router = APIRouter()


@router.post("/", response_model=dict)
def add_to_wishlist(
    wishlist_data: WishlistCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a product to user's wishlist."""
    wishlist_item = WishlistService.add_to_wishlist(db, current_user.id, wishlist_data)
    return success(data=wishlist_item.dict(), message="Product added to wishlist")


@router.delete("/{product_id}", response_model=dict)
def remove_from_wishlist(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove a product from user's wishlist."""
    WishlistService.remove_from_wishlist(db, current_user.id, product_id)
    return success(message="Product removed from wishlist")


@router.get("/", response_model=dict)
def get_user_wishlist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's wishlist with product details."""
    wishlist = WishlistService.get_user_wishlist(db, current_user.id)
    return success(data=wishlist.dict(), message="Wishlist retrieved successfully")


@router.get("/check/{product_id}", response_model=dict)
def check_wishlist_status(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if a product is in user's wishlist."""
    in_wishlist = WishlistService.check_in_wishlist(db, current_user.id, product_id)
    return success(data={"in_wishlist": in_wishlist}, message="Wishlist status checked")

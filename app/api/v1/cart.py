import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.schemas.cart import CartItemCreate, CartItemUpdate
from app.utils.response import success
from app.core.rate_limiter import limiter
from app.services import cart_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=dict)
@limiter.limit("60/minute")
def get_cart(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's cart"""
    try:
        payload = cart_service.get_cart(db, user_id=current_user.id)
        logger.info(
            "cart_loaded_authenticated",
            extra={"user_id": current_user.id, "item_count": payload["total_items"]},
        )
        return success(data=payload)
    except HTTPException:
        raise
    except Exception:
        logger.exception("cart_load_failed_authenticated", extra={"user_id": current_user.id})
        raise


@router.get("/user/{user_id}", response_model=dict)
@limiter.limit("60/minute")
def get_cart_by_user_id(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Compatibility cart route for user-id based storefront flows."""
    _ = request
    if current_user.id != user_id and getattr(current_user.role, "value", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized",
        )

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        payload = cart_service.get_cart(db, user_id=user_id)
        logger.info(
            "cart_loaded_by_user_id",
            extra={"user_id": user_id, "item_count": payload["total_items"]},
        )
        return success(data=payload)
    except HTTPException:
        raise
    except Exception:
        logger.exception("cart_load_failed_by_user_id", extra={"user_id": user_id})
        raise

@router.post("/items", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def add_to_cart(
    request: Request,
    cart_item: CartItemCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Add item to cart (authenticated users only)"""
    cart_item_record = cart_service.add_item(db, current_user=current_user, cart_item=cart_item)
    return success(
        data={"cart_item_id": cart_item_record.id},
        message="Cart updated" if cart_item_record.quantity > cart_item.quantity else "Item added to cart",
    )


@router.put("/items/{item_id}")
@limiter.limit("30/minute")
def update_cart_item(
    request: Request,
    item_id: int,
    update_data: CartItemUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update cart item quantity"""
    cart_service.update_item(db, current_user=current_user, item_id=item_id, update_data=update_data)
    return success(message="Cart item updated")


@router.delete("/items/{item_id}")
@limiter.limit("30/minute")
def remove_from_cart(
    request: Request,
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Remove item from cart"""
    cart_service.remove_item(db, current_user=current_user, item_id=item_id)
    return success(message="Item removed from cart")


@router.delete("/")
@limiter.limit("20/minute")
def clear_cart(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Clear entire cart"""
    cart_service.clear_cart(db, user_id=current_user.id)
    return success(message="Cart cleared")


@router.delete("", include_in_schema=False)
@limiter.limit("20/minute")
def clear_cart_without_trailing_slash(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    return clear_cart(request=request, current_user=current_user, db=db)

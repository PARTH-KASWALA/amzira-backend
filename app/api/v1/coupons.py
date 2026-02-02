from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.api.deps import get_current_user, require_admin
from app.models.user import User
from app.services.coupon_service import CouponService
from app.schemas.coupon import CouponCreate, CouponUpdate, CouponResponse, ApplyCouponRequest, ApplyCouponResponse
from app.utils.response import success, error

router = APIRouter()


@router.post("/", response_model=dict)
def create_coupon(
    coupon_data: CouponCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new coupon (admin only)."""
    try:
        coupon = CouponService.create_coupon(db, coupon_data)
        return success(data=coupon.dict(), message="Coupon created successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to create coupon", errors={"detail": str(e)})


@router.get("/", response_model=dict)
def list_coupons(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """List all coupons (admin only)."""
    try:
        coupons = CouponService.list_coupons(db, skip, limit)
        return success(data=[c.dict() for c in coupons], message="Coupons retrieved successfully")
    except Exception as e:
        return error(message="Failed to retrieve coupons", errors={"detail": str(e)})


@router.get("/{coupon_id}", response_model=dict)
def get_coupon(
    coupon_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get a coupon by ID (admin only)."""
    try:
        coupon = CouponService.get_coupon(db, coupon_id)
        return success(data=coupon.dict(), message="Coupon retrieved successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to retrieve coupon", errors={"detail": str(e)})


@router.put("/{coupon_id}", response_model=dict)
def update_coupon(
    coupon_id: int,
    coupon_data: CouponUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update a coupon (admin only)."""
    try:
        coupon = CouponService.update_coupon(db, coupon_id, coupon_data)
        return success(data=coupon.dict(), message="Coupon updated successfully")
    except HTTPException as e:
        return error(message=e.detail, errors={"detail": e.detail})
    except Exception as e:
        return error(message="Failed to update coupon", errors={"detail": str(e)})


@router.post("/validate", response_model=dict)
def validate_coupon(
    request: ApplyCouponRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Validate and preview coupon discount (public for authenticated users)."""
    try:
        result = CouponService.validate_and_apply_coupon(
            db, current_user.id, request.coupon_code, request.order_total
        )
        return success(data=result.dict(), message="Coupon validated")
    except Exception as e:
        return error(message="Failed to validate coupon", errors={"detail": str(e)})
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.api.deps import get_current_user, require_admin
from app.models.user import User
from app.services.coupon_service import CouponService
from app.schemas.coupon import CouponCreate, CouponUpdate, CouponResponse, ApplyCouponRequest, ApplyCouponResponse
from app.utils.response import success

router = APIRouter()


@router.post("/", response_model=dict)
def create_coupon(
    coupon_data: CouponCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new coupon (admin only)."""
    coupon = CouponService.create_coupon(db, coupon_data)
    return success(data=coupon.dict(), message="Coupon created successfully")


@router.get("/", response_model=dict)
def list_coupons(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """List all coupons (admin only)."""
    coupons = CouponService.list_coupons(db, skip, limit)
    return success(data=[c.dict() for c in coupons], message="Coupons retrieved successfully")


@router.get("/{coupon_id}", response_model=dict)
def get_coupon(
    coupon_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get a coupon by ID (admin only)."""
    coupon = CouponService.get_coupon(db, coupon_id)
    return success(data=coupon.dict(), message="Coupon retrieved successfully")


@router.put("/{coupon_id}", response_model=dict)
def update_coupon(
    coupon_id: int,
    coupon_data: CouponUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update a coupon (admin only)."""
    coupon = CouponService.update_coupon(db, coupon_id, coupon_data)
    return success(data=coupon.dict(), message="Coupon updated successfully")


@router.post("/validate", response_model=dict)
def validate_coupon(
    request: ApplyCouponRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Validate and preview coupon discount (public for authenticated users)."""
    result = CouponService.validate_and_apply_coupon(
        db, current_user.id, request.coupon_code, request.order_total
    )
    return success(data=result.dict(), message="Coupon validated")

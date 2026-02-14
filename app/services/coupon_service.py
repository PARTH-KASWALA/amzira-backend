from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from fastapi import HTTPException, status
from datetime import datetime
import structlog

from app.models.coupon import Coupon, DiscountType
from app.models.coupon_usage import CouponUsage
from app.models.order import Order, OrderStatus
from app.schemas.coupon import CouponCreate, CouponUpdate, CouponResponse, ApplyCouponResponse

logger = structlog.get_logger()


class CouponService:
    
    @staticmethod
    def create_coupon(db: Session, coupon_data: CouponCreate) -> CouponResponse:
        """Create a new coupon (admin only)."""
        # Check if code already exists
        existing = db.query(Coupon).filter(Coupon.code == coupon_data.code).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Coupon code already exists"
            )
        
        # Validate percentage value
        if coupon_data.discount_type == DiscountType.PERCENTAGE and coupon_data.discount_value > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Percentage discount cannot exceed 100%"
            )
        
        coupon = Coupon(
            code=coupon_data.code,
            description=coupon_data.description,
            discount_type=coupon_data.discount_type,
            discount_value=coupon_data.discount_value,
            min_order_value=coupon_data.min_order_value,
            max_discount=coupon_data.max_discount,
            usage_limit=coupon_data.usage_limit,
            per_user_limit=coupon_data.per_user_limit,
            expiry_date=coupon_data.expiry_date
        )
        
        db.add(coupon)
        db.commit()
        db.refresh(coupon)
        
        return CouponResponse.from_orm(coupon)
    
    @staticmethod
    def update_coupon(db: Session, coupon_id: int, coupon_data: CouponUpdate) -> CouponResponse:
        """Update a coupon (admin only)."""
        coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
        if not coupon:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Coupon not found"
            )
        
        # Update fields
        update_data = coupon_data.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(coupon, key, value)
        
        # Validate percentage
        if coupon.discount_type == DiscountType.PERCENTAGE and coupon.discount_value > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Percentage discount cannot exceed 100%"
            )
        
        db.commit()
        db.refresh(coupon)
        
        return CouponResponse.from_orm(coupon)
    
    @staticmethod
    def delete_coupon(db: Session, coupon_id: int):
        """Delete a coupon (admin only)."""
        coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
        if not coupon:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Coupon not found"
            )
        
        db.delete(coupon)
        db.commit()
    
    @staticmethod
    def get_coupon(db: Session, coupon_id: int) -> CouponResponse:
        """Get a coupon by ID."""
        coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
        if not coupon:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Coupon not found"
            )
        
        return CouponResponse.from_orm(coupon)
    
    @staticmethod
    def list_coupons(db: Session, skip: int = 0, limit: int = 100) -> list[CouponResponse]:
        """List all coupons."""
        coupons = db.query(Coupon).offset(skip).limit(limit).all()
        return [CouponResponse.from_orm(coupon) for coupon in coupons]
    
    @staticmethod
    def validate_and_apply_coupon(
        db: Session, 
        user_id: int, 
        coupon_code: str, 
        order_total: float
    ) -> ApplyCouponResponse:
        """Validate and calculate discount for a coupon."""
        # Find coupon
        coupon = db.query(Coupon).filter(
            and_(Coupon.code == coupon_code, Coupon.is_active == True)
        ).first()
        
        if not coupon:
            return ApplyCouponResponse(
                valid=False,
                discount_amount=0.0,
                final_total=order_total,
                message="Invalid or inactive coupon code"
            )
        
        # Check expiry
        if coupon.expiry_date and coupon.expiry_date < datetime.utcnow():
            return ApplyCouponResponse(
                valid=False,
                discount_amount=0.0,
                final_total=order_total,
                message="Coupon has expired"
            )
        
        # Check min order value
        if order_total < coupon.min_order_value:
            return ApplyCouponResponse(
                valid=False,
                discount_amount=0.0,
                final_total=order_total,
                message=f"Minimum order value of ₹{coupon.min_order_value} required"
            )
        
        # Check global usage limit
        if coupon.usage_limit and coupon.used_count >= coupon.usage_limit:
            return ApplyCouponResponse(
                valid=False,
                discount_amount=0.0,
                final_total=order_total,
                message="Coupon usage limit exceeded"
            )
        
        # Check per-user usage limit
        user_usage_count = db.query(func.count(CouponUsage.id)).filter(
            and_(CouponUsage.coupon_id == coupon.id, CouponUsage.user_id == user_id)
        ).scalar()
        
        if user_usage_count >= coupon.per_user_limit:
            return ApplyCouponResponse(
                valid=False,
                discount_amount=0.0,
                final_total=order_total,
                message="You have already used this coupon the maximum allowed times"
            )
        
        # Calculate discount
        if coupon.discount_type == DiscountType.PERCENTAGE:
            discount_amount = (order_total * coupon.discount_value) / 100
            if coupon.max_discount and discount_amount > coupon.max_discount:
                discount_amount = coupon.max_discount
        else:  # FIXED
            discount_amount = min(coupon.discount_value, order_total)
        
        final_total = max(0, order_total - discount_amount)
        
        return ApplyCouponResponse(
            valid=True,
            discount_amount=discount_amount,
            final_total=final_total,
            message="Coupon applied successfully",
            coupon_details=CouponResponse.from_orm(coupon)
        )
    
    @staticmethod
    def apply_coupon_to_order(db: Session, order_id: int, user_id: int, coupon_code: str):
        """Apply coupon to an existing order (during checkout)."""
        try:
            order = (
                db.query(Order)
                .filter(and_(Order.id == order_id, Order.user_id == user_id))
                .with_for_update()
                .first()
            )
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Order not found",
                )
            if order.status != OrderStatus.PENDING:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot apply coupon to non-pending order",
                )

            coupon = (
                db.query(Coupon)
                .filter(and_(Coupon.code == coupon_code, Coupon.is_active == True))
                .with_for_update()
                .first()
            )
            if not coupon:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or inactive coupon code",
                )

            if coupon.expiry_date and coupon.expiry_date < datetime.utcnow():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon has expired")
            if order.subtotal < coupon.min_order_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Minimum order value of ₹{coupon.min_order_value} required",
                )
            if coupon.usage_limit and coupon.used_count >= coupon.usage_limit:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon usage limit exceeded")

            user_usage_count = db.query(func.count(CouponUsage.id)).filter(
                and_(CouponUsage.coupon_id == coupon.id, CouponUsage.user_id == user_id)
            ).scalar() or 0
            if user_usage_count >= coupon.per_user_limit:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You have already used this coupon the maximum allowed times",
                )

            existing_order_usage = db.query(CouponUsage).filter(CouponUsage.order_id == order_id).first()
            if existing_order_usage:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Coupon already applied to this order",
                )

            if coupon.discount_type == DiscountType.PERCENTAGE:
                discount_amount = (order.subtotal * coupon.discount_value) / 100
                if coupon.max_discount and discount_amount > coupon.max_discount:
                    discount_amount = coupon.max_discount
            else:
                discount_amount = min(coupon.discount_value, order.subtotal)

            final_total = max(0, order.subtotal - discount_amount)
            order.discount_amount = discount_amount
            order.coupon_code = coupon.code
            order.total_amount = final_total

            coupon.used_count += 1
            db.add(
                CouponUsage(
                    coupon_id=coupon.id,
                    user_id=user_id,
                    order_id=order_id,
                )
            )
            db.commit()
            db.refresh(order)
            return order
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("coupon_apply_failed", order_id=order_id, user_id=user_id, coupon_code=coupon_code)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to apply coupon",
            )

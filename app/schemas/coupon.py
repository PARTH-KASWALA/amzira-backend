from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.coupon import DiscountType


class CouponCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None
    discount_type: DiscountType
    discount_value: float = Field(..., gt=0)
    min_order_value: float = Field(default=0.0, ge=0)
    max_discount: Optional[float] = Field(None, gt=0)
    usage_limit: Optional[int] = Field(None, gt=0)
    per_user_limit: int = Field(default=1, ge=1)
    expiry_date: Optional[datetime] = None


class CouponUpdate(BaseModel):
    description: Optional[str] = None
    discount_type: Optional[DiscountType] = None
    discount_value: Optional[float] = Field(None, gt=0)
    min_order_value: Optional[float] = Field(None, ge=0)
    max_discount: Optional[float] = Field(None, gt=0)
    usage_limit: Optional[int] = Field(None, gt=0)
    per_user_limit: Optional[int] = Field(None, ge=1)
    expiry_date: Optional[datetime] = None
    is_active: Optional[bool] = None


class CouponResponse(BaseModel):
    id: int
    code: str
    description: Optional[str]
    discount_type: DiscountType
    discount_value: float
    min_order_value: float
    max_discount: Optional[float]
    usage_limit: Optional[int]
    used_count: int
    per_user_limit: int
    is_active: bool
    expiry_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ApplyCouponRequest(BaseModel):
    coupon_code: str
    order_total: float


class ApplyCouponResponse(BaseModel):
    valid: bool
    discount_amount: float
    final_total: float
    message: str
    coupon_details: Optional[CouponResponse] = None
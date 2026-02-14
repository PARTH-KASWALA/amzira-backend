from typing import List, Literal, Optional
from datetime import datetime
import uuid

import bleach
from pydantic import BaseModel, Field, field_validator


class OrderItemResponse(BaseModel):
    id: int
    product_name: str
    variant_details: str
    quantity: int
    unit_price: float
    total_price: float
    
    class Config:
        from_attributes = True


class OrderCreate(BaseModel):
    shipping_address_id: int = Field(..., gt=0)
    billing_address_id: int = Field(..., gt=0)
    payment_method: Literal["razorpay", "cod"] = "razorpay"
    customer_notes: Optional[str] = None
    idempotency_key: str = Field(..., min_length=36, max_length=64)

    @field_validator("customer_notes")
    @classmethod
    def validate_notes(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        sanitized = bleach.clean(value, tags=[], attributes={}, strip=True).strip()
        if len(sanitized) > 500:
            raise ValueError("Notes too long (max 500 chars)")
        return sanitized

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        parsed = uuid.UUID(value)
        return str(parsed)


class OrderResponse(BaseModel):
    id: int
    order_number: str
    status: str
    subtotal: float
    tax_amount: float
    shipping_charge: float
    total_amount: float
    items: List[OrderItemResponse]
    created_at: datetime
    tracking_number: Optional[str] = None
    
    class Config:
        from_attributes = True

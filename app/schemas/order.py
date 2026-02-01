from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


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
    shipping_address_id: int
    billing_address_id: int
    payment_method: str = "razorpay"
    customer_notes: Optional[str] = None


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
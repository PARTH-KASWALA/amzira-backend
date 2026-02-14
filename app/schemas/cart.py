from pydantic import BaseModel, Field
from typing import List, Optional


class CartItemCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = Field(default=1, ge=1, le=10)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, le=10)


class CartItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_image: str
    variant_id: int
    variant_details: str
    quantity: int
    unit_price: float
    total_price: float
    
    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    items: List[CartItemResponse]
    subtotal: float
    total_items: int

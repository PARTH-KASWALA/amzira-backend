from pydantic import BaseModel
from typing import List


class CartItemCreate(BaseModel):
    product_id: int
    variant_id: int
    quantity: int = 1


class CartItemUpdate(BaseModel):
    quantity: int


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
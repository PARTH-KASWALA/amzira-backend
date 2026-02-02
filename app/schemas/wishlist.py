from pydantic import BaseModel
from typing import List
from datetime import datetime


class WishlistCreate(BaseModel):
    product_id: int


class WishlistResponse(BaseModel):
    id: int
    user_id: int
    product_id: int
    created_at: datetime
    product_name: str
    product_slug: str
    product_price: float
    product_image: str | None

    class Config:
        from_attributes = True


class WishlistListResponse(BaseModel):
    wishlist_items: List[WishlistResponse]
    total: int
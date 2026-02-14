from typing import List

from pydantic import BaseModel, Field


class StockCheckItem(BaseModel):
    variant_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


class StockCheckRequest(BaseModel):
    items: List[StockCheckItem]


class InsufficientStockItem(BaseModel):
    variant_id: int
    available_quantity: int
    requested_quantity: int
    message: str


class StockCheckResponse(BaseModel):
    available: bool
    items: List[InsufficientStockItem]
    insufficient_items: List[InsufficientStockItem]

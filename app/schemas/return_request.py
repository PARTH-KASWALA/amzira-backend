from pydantic import BaseModel
from typing import Optional
from app.models.return_request import ReturnReason, ReturnStatus


class ReturnRequestCreate(BaseModel):
    order_id: int
    order_item_id: int
    reason: ReturnReason
    description: Optional[str] = None


class ReturnRequestResponse(BaseModel):
    id: int
    order_id: int
    order_item_id: int
    user_id: int
    reason: ReturnReason
    status: ReturnStatus
    refund_amount: Optional[float] = None

    class Config:
        orm_mode = True

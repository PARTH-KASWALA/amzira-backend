from pydantic import BaseModel
from uuid import UUID
from typing import Optional
from app.models.return_request import ReturnReason, ReturnStatus


class ReturnRequestCreate(BaseModel):
    order_id: UUID
    order_item_id: UUID
    reason: ReturnReason
    description: Optional[str] = None


class ReturnRequestResponse(BaseModel):
    id: UUID
    order_id: UUID
    order_item_id: UUID
    user_id: UUID
    reason: ReturnReason
    status: ReturnStatus
    refund_amount: Optional[float] = None

    class Config:
        orm_mode = True

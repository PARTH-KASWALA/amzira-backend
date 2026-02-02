from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.models.order import OrderStatus


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    tracking_number: Optional[str] = None
    carrier_name: Optional[str] = None
    estimated_delivery_date: Optional[datetime] = None
    notes: Optional[str] = None


class OrderStatusHistoryResponse(BaseModel):
    id: int
    order_id: int
    old_status: Optional[str]
    new_status: str
    changed_by: Optional[int]
    changer_name: Optional[str]  # Name of the admin who changed it
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class OrderTrackingResponse(BaseModel):
    order_id: int
    order_number: str
    current_status: str
    tracking_number: Optional[str]
    carrier_name: Optional[str]
    estimated_delivery_date: Optional[datetime]
    status_history: List[OrderStatusHistoryResponse]

    class Config:
        from_attributes = True
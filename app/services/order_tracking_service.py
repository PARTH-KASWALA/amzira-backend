from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import List, Optional
from datetime import datetime

from app.models.order import Order, OrderStatus
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User
from app.schemas.order_tracking import OrderStatusUpdate, OrderTrackingResponse, OrderStatusHistoryResponse
from app.services.return_service import mark_order_delivered, refresh_return_status, utc_now
from app.utils.response import success, error

ALLOWED_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PLACED: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.OUT_FOR_DELIVERY,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PROCESSING: {
        OrderStatus.SHIPPED,
        OrderStatus.OUT_FOR_DELIVERY,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.SHIPPED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.RETURN_REQUESTED, OrderStatus.RETURNED, OrderStatus.REFUNDED},
    OrderStatus.RETURN_REQUESTED: {OrderStatus.RETURNED, OrderStatus.REFUNDED},
    OrderStatus.RETURNED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

PUBLIC_STATUS_SEQUENCE = [
    OrderStatus.PLACED,
    OrderStatus.CONFIRMED,
    OrderStatus.SHIPPED,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
    OrderStatus.RETURN_REQUESTED,
    OrderStatus.RETURNED,
]


def normalize_public_status(status: OrderStatus | str | None) -> OrderStatus:
    if status is None:
        return OrderStatus.PLACED

    if isinstance(status, OrderStatus):
        current = status
    else:
        normalized = str(status).strip().lower()
        if normalized == OrderStatus.PENDING.value:
            return OrderStatus.PLACED
        if normalized == OrderStatus.PROCESSING.value:
            return OrderStatus.CONFIRMED
        current = OrderStatus(normalized)

    if current == OrderStatus.PENDING:
        return OrderStatus.PLACED
    if current == OrderStatus.PROCESSING:
        return OrderStatus.CONFIRMED
    return current


def build_status_timeline(status: OrderStatus | str | None) -> list[dict[str, str | bool]]:
    current = normalize_public_status(status)
    if current not in PUBLIC_STATUS_SEQUENCE:
        current = OrderStatus.DELIVERED if current == OrderStatus.REFUNDED else OrderStatus.PLACED

    current_index = PUBLIC_STATUS_SEQUENCE.index(current)
    timeline = []
    for index, step in enumerate(PUBLIC_STATUS_SEQUENCE):
        timeline.append(
            {
                "status": step.name,
                "label": step.name.replace("_", " ").title(),
                "completed": index <= current_index,
                "current": index == current_index,
            }
        )
    return timeline


class OrderTrackingService:
    
    @staticmethod
    def update_order_status(
        db: Session, 
        order_id: int, 
        status_update: OrderStatusUpdate, 
        changed_by: Optional[int] = None
    ) -> Order:
        """Update order status with history tracking. Admin only."""
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        current_status = order.status if isinstance(order.status, OrderStatus) else OrderStatus(str(order.status))
        old_status = current_status.value
        target_status = status_update.status

        if current_status != target_status and target_status not in ALLOWED_STATUS_TRANSITIONS.get(current_status, set()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot transition order from {current_status.value} to {target_status.value}",
            )

        # Update order fields
        order.status = target_status
        if status_update.tracking_number:
            order.tracking_number = status_update.tracking_number
        if status_update.carrier_name:
            order.carrier_name = status_update.carrier_name
            order.courier_name = status_update.carrier_name
        if status_update.estimated_delivery_date:
            order.estimated_delivery_date = status_update.estimated_delivery_date
        if target_status == OrderStatus.DELIVERED:
            mark_order_delivered(order)
        elif target_status == OrderStatus.RETURN_REQUESTED:
            order.return_status = "requested"
        elif target_status == OrderStatus.RETURNED:
            order.return_status = "expired"
        else:
            refresh_return_status(order)
        
        # Create status history entry
        history_entry = OrderStatusHistory(
            order_id=order_id,
            old_status=old_status,
            new_status=status_update.status.value,
            changed_by=changed_by,
            notes=status_update.notes,
            created_at=utc_now(),
        )
        
        db.add(history_entry)
        db.commit()
        db.refresh(order)
        
        return order
    
    @staticmethod
    def get_order_tracking(db: Session, order_id: int, user_id: int, user_role: str) -> OrderTrackingResponse:
        """Get order tracking information. Users can only see their own orders, admins can see all."""
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check ownership or admin
        if order.user_id != user_id and user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only track your own orders"
            )
        
        # Get status history
        history = db.query(OrderStatusHistory, User.full_name.label('changer_name')).outerjoin(
            User, OrderStatusHistory.changed_by == User.id
        ).filter(OrderStatusHistory.order_id == order_id).order_by(OrderStatusHistory.created_at).all()
        
        history_responses = [
            OrderStatusHistoryResponse(
                id=h.OrderStatusHistory.id,
                order_id=h.OrderStatusHistory.order_id,
                old_status=h.OrderStatusHistory.old_status,
                new_status=h.OrderStatusHistory.new_status,
                changed_by=h.OrderStatusHistory.changed_by,
                changer_name=h.changer_name,
                notes=h.OrderStatusHistory.notes,
                created_at=h.OrderStatusHistory.created_at
            ) for h in history
        ]
        
        return OrderTrackingResponse(
            order_id=order.id,
            order_number=order.order_number,
            current_status=normalize_public_status(order.status).name,
            tracking_number=order.tracking_number,
            carrier_name=order.courier_name or order.carrier_name,
            estimated_delivery_date=order.estimated_delivery_date,
            status_history=history_responses,
            tracking_url=order.tracking_url,
            shipment_id=order.shipment_id,
            courier_name=order.courier_name or order.carrier_name,
            timeline=build_status_timeline(order.status),
        )
    
    @staticmethod
    def get_user_orders_tracking(db: Session, user_id: int) -> List[OrderTrackingResponse]:
        """Get tracking info for all user's orders."""
        orders = db.query(Order).filter(Order.user_id == user_id).all()
        
        tracking_info = []
        for order in orders:
            # Get latest history for each order
            history = db.query(OrderStatusHistory, User.full_name.label('changer_name')).outerjoin(
                User, OrderStatusHistory.changed_by == User.id
            ).filter(OrderStatusHistory.order_id == order.id).order_by(OrderStatusHistory.created_at).all()
            
            history_responses = [
                OrderStatusHistoryResponse(
                    id=h.OrderStatusHistory.id,
                    order_id=h.OrderStatusHistory.order_id,
                    old_status=h.OrderStatusHistory.old_status,
                    new_status=h.OrderStatusHistory.new_status,
                    changed_by=h.OrderStatusHistory.changed_by,
                    changer_name=h.changer_name,
                    notes=h.OrderStatusHistory.notes,
                    created_at=h.OrderStatusHistory.created_at
                ) for h in history
            ]
            
            tracking_info.append(OrderTrackingResponse(
                order_id=order.id,
                order_number=order.order_number,
                current_status=normalize_public_status(order.status).name,
                tracking_number=order.tracking_number,
                carrier_name=order.courier_name or order.carrier_name,
                estimated_delivery_date=order.estimated_delivery_date,
                status_history=history_responses,
                tracking_url=order.tracking_url,
                shipment_id=order.shipment_id,
                courier_name=order.courier_name or order.carrier_name,
                timeline=build_status_timeline(order.status),
            ))
        
        return tracking_info

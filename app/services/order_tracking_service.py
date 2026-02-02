from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import List, Optional

from app.models.order import Order, OrderStatus
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User
from app.schemas.order_tracking import OrderStatusUpdate, OrderTrackingResponse, OrderStatusHistoryResponse
from app.utils.response import success, error


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
        
        old_status = order.status.value
        
        # Update order fields
        order.status = status_update.status
        if status_update.tracking_number:
            order.tracking_number = status_update.tracking_number
        if status_update.carrier_name:
            order.carrier_name = status_update.carrier_name
        if status_update.estimated_delivery_date:
            order.estimated_delivery_date = status_update.estimated_delivery_date
        
        # Create status history entry
        history_entry = OrderStatusHistory(
            order_id=order_id,
            old_status=old_status,
            new_status=status_update.status.value,
            changed_by=changed_by,
            notes=status_update.notes
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
            current_status=order.status.value,
            tracking_number=order.tracking_number,
            carrier_name=order.carrier_name,
            estimated_delivery_date=order.estimated_delivery_date,
            status_history=history_responses
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
                current_status=order.status.value,
                tracking_number=order.tracking_number,
                carrier_name=order.carrier_name,
                estimated_delivery_date=order.estimated_delivery_date,
                status_history=history_responses
            ))
        
        return tracking_info
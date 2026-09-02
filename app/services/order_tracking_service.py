from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import List, Optional
from datetime import datetime

from app.models.order import Order, OrderStatus
from app.models.payment import PaymentMethod, PaymentStatus
from app.models.product import ProductVariant
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
    def restore_inventory_once(db: Session, order: Order) -> None:
        if not order.stock_deducted:
            return

        variant_ids = sorted({item.variant_id for item in order.items})
        locked_variants = {
            variant.id: variant
            for variant in (
                db.query(ProductVariant)
                .filter(ProductVariant.id.in_(variant_ids))
                .with_for_update()
                .all()
            )
        }
        for item in order.items:
            variant = locked_variants.get(item.variant_id)
            if variant is not None:
                variant.stock_quantity += item.quantity
        order.stock_deducted = False
    
    @staticmethod
    def update_order_status(
        db: Session, 
        order_id: int, 
        status_update: OrderStatusUpdate, 
        changed_by: Optional[int] = None
    ) -> Order:
        """Update order status with history tracking. Admin only."""
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .first()
        )
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

        if target_status in {OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY}:
            effective_tracking = status_update.tracking_number or order.tracking_number or order.awb_code
            effective_carrier = status_update.carrier_name or order.carrier_name or order.courier_name
            if not effective_tracking or not effective_carrier:
                raise HTTPException(
                    status_code=422,
                    detail="Tracking number and courier are required before an order can be shipped",
                )

        if target_status == OrderStatus.CANCELLED and current_status != OrderStatus.CANCELLED:
            payment = order.payment
            if (
                payment is not None
                and payment.payment_method == PaymentMethod.RAZORPAY
                and payment.payment_status == PaymentStatus.SUCCESS
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Paid orders require a refund before cancellation can be completed",
                )
            OrderTrackingService.restore_inventory_once(db, order)
            order.expires_at = None

        previous_tracking_number = order.tracking_number
        previous_carrier_name = order.carrier_name
        previous_eta = order.estimated_delivery_date
        previous_admin_notes = order.admin_notes

        # Update order fields
        order.status = target_status
        if status_update.tracking_number:
            order.tracking_number = status_update.tracking_number
        if status_update.carrier_name:
            order.carrier_name = status_update.carrier_name
            order.courier_name = status_update.carrier_name
        if status_update.estimated_delivery_date:
            order.estimated_delivery_date = status_update.estimated_delivery_date
        if status_update.notes:
            order.admin_notes = status_update.notes
        if target_status == OrderStatus.DELIVERED and (
            current_status != OrderStatus.DELIVERED or order.delivered_at is None
        ):
            mark_order_delivered(order)
        elif target_status == OrderStatus.RETURN_REQUESTED:
            order.return_status = "requested"
        elif target_status == OrderStatus.RETURNED:
            order.return_status = "expired"
        else:
            refresh_return_status(order)
        
        changed = (
            current_status != target_status
            or previous_tracking_number != order.tracking_number
            or previous_carrier_name != order.carrier_name
            or previous_eta != order.estimated_delivery_date
            or previous_admin_notes != order.admin_notes
        )
        if not changed:
            return order

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
        
        expose_admin_history = user_role == "admin"
        history_responses = [
            OrderStatusHistoryResponse(
                id=h.OrderStatusHistory.id,
                order_id=h.OrderStatusHistory.order_id,
                old_status=h.OrderStatusHistory.old_status,
                new_status=h.OrderStatusHistory.new_status,
                changed_by=h.OrderStatusHistory.changed_by if expose_admin_history else None,
                changer_name=h.changer_name if expose_admin_history else None,
                notes=h.OrderStatusHistory.notes if expose_admin_history else None,
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
                    changed_by=None,
                    changer_name=None,
                    notes=None,
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

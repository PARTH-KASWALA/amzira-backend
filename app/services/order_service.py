from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List

from app.models.order import Order, OrderStatus

def auto_cancel_pending_orders(db: Session) -> int:
    """
    Cancel orders that have been pending for more than 30 minutes.

    Args:
        db (Session): Database session

    Returns:
        int: Number of orders cancelled
    """
    cutoff_time = datetime.utcnow() - timedelta(minutes=30)

    pending_orders: List[Order] = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING,
            Order.created_at < cutoff_time
        )
        .all()
    )

    for order in pending_orders:
        order.status = OrderStatus.CANCELLED

    db.commit()
    return len(pending_orders)

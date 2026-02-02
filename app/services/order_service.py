from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus





def auto_cancel_pending_orders(db: Session):
    """Background task: Cancel orders pending for > 30 minutes"""
    cutoff_time = datetime.utcnow() - timedelta(minutes=30)
    
    pending_orders = db.query(Order).filter(
        Order.status == OrderStatus.PENDING,
        Order.created_at < cutoff_time
    ).all()
    
    for order in pending_orders:
        order.status = OrderStatus.CANCELLED
    # Stock was never deducted at order creation, so no restoration is necessary.
    db.commit()
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List
import structlog

from app.models.order import Order, OrderStatus
from app.models.product import ProductVariant

logger = structlog.get_logger()


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
            Order.status == OrderStatus.PENDING
        )
        .all()
    )

    cancelled_count = 0
    for order in pending_orders:
        is_expired = False
        if order.expires_at:
            is_expired = order.expires_at <= datetime.utcnow()
        else:
            is_expired = order.created_at < cutoff_time

        if not is_expired:
            continue

        if order.stock_deducted:
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
                if variant:
                    variant.stock_quantity += item.quantity
            order.stock_deducted = False

        order.status = OrderStatus.CANCELLED
        order.expires_at = None
        logger.info(
            "order_expired",
            order_id=order.id,
            user_id=order.user_id,
            previous_status=OrderStatus.PENDING.value,
        )
        cancelled_count += 1

    db.commit()
    return cancelled_count

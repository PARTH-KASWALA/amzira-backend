# app/tasks/order_tasks.py

from datetime import datetime, timedelta
import logging
from celery import shared_task
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.checkout_payment_intent import CheckoutPaymentIntent, CheckoutPaymentIntentStatus
from app.models.order import Order
from app.services.order_service import auto_cancel_pending_orders
from app.services.checkout_payment_service import expire_checkout_intents
from app.services.shiprocket import fulfill_order

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def cleanup_expired_orders(self):
    """
    Cancel orders that are pending for more than 30 minutes.
    Runs periodically via Celery Beat.
    """
    db = SessionLocal()
    try:
        auto_cancel_pending_orders(db)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@shared_task(bind=True, max_retries=3)
def cancel_expired_orders(self):
    """Backward-compatible task name."""
    return cleanup_expired_orders(self)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fulfill_order_async(self, order_id: int):
    """Fulfill an order via Shiprocket in the background."""
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if order is None:
            return {"fulfilled": False, "reason": "order_not_found"}

        result = fulfill_order(order)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


def dispatch_fulfill_order(order_id: int) -> None:
    """Queue Shiprocket fulfillment in production and run inline in tests."""
    if settings.TESTING or settings.CELERY_TASK_ALWAYS_EAGER:
        try:
            fulfill_order_async.run(order_id)
        except Exception:
            logger.exception("shiprocket_fulfillment_inline_failed order_id=%s", order_id)
        return
    fulfill_order_async.apply_async(args=[order_id], ignore_result=True)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def release_expired_payment_reservations(self):
    """Expire checkout sessions and atomically return their reserved inventory."""
    db = SessionLocal()
    try:
        released = expire_checkout_intents(db)
        db.commit()
        return {"released": released}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def purge_expired_payment_intents(self):
    """Delete expired or failed checkout intents older than 24 hours."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=1)
        deleted = (
            db.query(CheckoutPaymentIntent)
            .filter(
                CheckoutPaymentIntent.status.in_(
                    [
                        CheckoutPaymentIntentStatus.EXPIRED,
                        CheckoutPaymentIntentStatus.FAILED,
                    ]
                ),
                CheckoutPaymentIntent.expires_at.isnot(None),
                CheckoutPaymentIntent.expires_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted": deleted}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()

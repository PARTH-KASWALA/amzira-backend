# app/tasks/order_tasks.py

from datetime import timedelta
from celery import shared_task
from app.db.session import SessionLocal
from app.services.order_service import auto_cancel_pending_orders


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

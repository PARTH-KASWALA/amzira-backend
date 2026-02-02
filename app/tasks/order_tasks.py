from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.services.order_service import auto_cancel_pending_orders


@celery_app.task(name="app.tasks.order_tasks.cancel_expired_orders")
def cancel_expired_orders():
    db = SessionLocal()
    try:
        auto_cancel_pending_orders(db)
    finally:
        db.close()

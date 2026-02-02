from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

broker = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
backend = getattr(settings, "CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "amzira",
    broker=broker,
    backend=backend,
)

celery_app.conf.beat_schedule = {
    "cancel-expired-orders": {
        "task": "app.tasks.order_tasks.cancel_expired_orders",
        "schedule": crontab(minute="*/5"),
    }
}

celery_app.conf.timezone = "UTC"

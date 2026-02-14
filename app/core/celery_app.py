from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "amzira",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.email_tasks", "app.tasks.order_tasks", "app.tasks.security_tasks"]
)

# Auto-discover tasks from app.tasks
celery_app.autodiscover_tasks(["app.tasks"])

# Celery Configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    task_track_started=True,

    task_time_limit=300,        # Hard limit (5 min)
    task_soft_time_limit=240,   # Soft limit (4 min)

    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,

    task_acks_late=True,
    task_reject_on_worker_lost=True,

    result_expires=3600,  # 1 hour
)

# OPTIONAL (future-proof)
celery_app.conf.task_routes = {
    "app.tasks.email_tasks.*": {"queue": "emails"},
}



from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "cancel-expired-orders-every-5-min": {
        "task": "app.tasks.order_tasks.cleanup_expired_orders",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup-expired-blacklisted-tokens-daily": {
        "task": "app.tasks.security_tasks.cleanup_expired_blacklisted_tokens",
        "schedule": crontab(hour=3, minute=0),
    },
}

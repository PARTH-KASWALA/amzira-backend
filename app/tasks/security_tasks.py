from datetime import datetime

from celery import shared_task

from app.db.session import SessionLocal
from app.models.token_blacklist import TokenBlacklist


@shared_task(bind=True, max_retries=3)
def cleanup_expired_blacklisted_tokens(self):
    """Delete expired token blacklist rows to keep the table bounded."""
    db = SessionLocal()
    try:
        deleted = (
            db.query(TokenBlacklist)
            .filter(TokenBlacklist.expires_at < datetime.utcnow())
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted": deleted}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()

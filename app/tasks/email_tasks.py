from celery import Task
from celery.utils.log import get_task_logger
from email.message import EmailMessage
from typing import Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.utils.email import _send_email_smtp
from app.utils.email_templates import (
    order_confirmation_template,
    order_shipped_template,
    order_delivered_template,
    password_reset_template,
)

logger = get_task_logger(__name__)


# -------------------------------
# Base Task (Retry-safe)
# -------------------------------
class EmailTask(Task):
    """
    Base email task with retries and backoff.
    Prevents email loss on temporary SMTP failures.
    """
    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": 3}
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes
    retry_jitter = True
    acks_late = True  # retry if worker crashes


# -------------------------------
# Helper: Build Email
# -------------------------------
def build_email(
    *,
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    from_email: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email or f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
    msg["To"] = to
    msg.set_content(text)

    if html:
        msg.add_alternative(html, subtype="html")

    return msg


# -------------------------------
# Order Confirmation
# -------------------------------
@celery_app.task(base=EmailTask, bind=True)
def send_order_confirmation(self, order_id: int):
    from app.db.session import SessionLocal
    from app.models.order import Order

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order or not order.user:
            logger.error("order_confirmation_failed", order_id=order_id)
            return

        html = order_confirmation_template(order, order.user)

        msg = build_email(
            to=order.user.email,
            subject=f"Order Confirmed - {order.order_number}",
            text=f"Your order {order.order_number} has been confirmed.",
            html=html,
            from_email=settings.EMAILS_FROM_ORDERS,
        )

        _send_email_smtp(msg)
        logger.info("order_confirmation_sent", email=order.user.email)

    except Exception as exc:
        logger.exception("order_confirmation_error", exc_info=exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# -------------------------------
# Order Shipped
# -------------------------------
@celery_app.task(base=EmailTask, bind=True)
def send_order_shipped(self, order_id: int, tracking_number: str):
    from app.db.session import SessionLocal
    from app.models.order import Order

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order or not order.user:
            return

        html = order_shipped_template(order, order.user, tracking_number)

        msg = build_email(
            to=order.user.email,
            subject=f"Order Shipped - {order.order_number}",
            text=f"Your order {order.order_number} has been shipped.",
            html=html,
            from_email=settings.EMAILS_FROM_SHIPPING,
        )

        _send_email_smtp(msg)
        logger.info("order_shipped_sent", order_id=order_id)

    except Exception as exc:
        logger.exception("order_shipped_error", exc_info=exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# -------------------------------
# Order Delivered
# -------------------------------
@celery_app.task(base=EmailTask, bind=True)
def send_order_delivered(self, order_id: int):
    from app.db.session import SessionLocal
    from app.models.order import Order

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order or not order.user:
            return

        html = order_delivered_template(order, order.user)

        msg = build_email(
            to=order.user.email,
            subject=f"Order Delivered - {order.order_number}",
            text=f"Your order {order.order_number} has been delivered.",
            html=html,
            from_email=settings.EMAILS_FROM_SHIPPING,
        )

        _send_email_smtp(msg)

    except Exception as exc:
        logger.exception("order_delivered_error", exc_info=exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# -------------------------------
# Password Reset
# -------------------------------
@celery_app.task(base=EmailTask, bind=True)
def send_password_reset(self, user_email: str, reset_token: str):
    try:
        html = password_reset_template(reset_token)

        msg = build_email(
            to=user_email,
            subject="Reset Your AMZIRA Password",
            text="Click the link to reset your password.",
            html=html,
            from_email=settings.EMAILS_FROM_SUPPORT,
        )

        _send_email_smtp(msg)
        logger.info("password_reset_sent", email=user_email)

    except Exception as exc:
        logger.exception("password_reset_error", exc_info=exc)
        raise self.retry(exc=exc)

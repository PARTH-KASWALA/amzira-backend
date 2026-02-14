import logging
import smtplib
from email.message import EmailMessage
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_email_smtp(msg: EmailMessage) -> None:
    """
    Send an email message over SMTP.
    This is intended to be called from Celery workers, not request handlers.
    """
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)


def send_email_async(to_email: str, subject: str, body: str, html: Optional[str] = None) -> None:
    """
    Queue email for asynchronous sending via Celery.
    
    This replaces the old daemon thread approach with a proper task queue
    that ensures emails are never lost during server restarts.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Plain text email body
        html: Optional HTML email body
    """
    try:
        from app.tasks.email_tasks import send_email_task
        
        # Queue the email task - it will be processed by Celery worker
        result = send_email_task.delay(to_email, subject, body, html)
        
        logger.info(
            "Email queued for sending",
            extra={
                "to": to_email,
                "subject": subject,
                "task_id": result.id
            }
        )
        
    except Exception as exc:
        logger.exception(
            "Failed to queue email task",
            extra={
                "to": to_email,
                "subject": subject,
                "error": str(exc)
            }
        )


def send_order_confirmation_email(order, to_email: Optional[str] = None) -> None:
    """
    Send order confirmation email using HTML template (non-blocking via Celery).
    
    Args:
        order: Order object with order details
        to_email: Optional override email address
    """
    user = getattr(order, "user", None)
    recipient = to_email or (user.email if user else None)

    if not recipient:
        logger.warning(
            "No recipient for order confirmation",
            extra={"order_id": getattr(order, "id", None)}
        )
        return

    if not user:
        logger.warning(
            "Order has no user attached",
            extra={"order_id": getattr(order, "id", None)}
        )
        return

    order_number = getattr(order, 'order_number', 'Order')
    subject = f"Order Confirmed - {order_number}"
    body = f"Your order {order_number} has been confirmed."

    try:
        from app.utils.email_templates import order_confirmation_template
        
        # Generate HTML email
        html = order_confirmation_template(order, user)
        
        # Queue email via Celery
        send_email_async(recipient, subject, body, html)
        
        logger.info(
            "Order confirmation email queued",
            extra={
                "order_id": getattr(order, "id", None),
                "order_number": order_number,
                "recipient": recipient
            }
        )
        
    except Exception as exc:
        logger.exception(
            "Failed to queue order confirmation email",
            extra={
                "order_id": getattr(order, "id", None),
                "order_number": order_number,
                "error": str(exc)
            }
        )


# Legacy compatibility - remove daemon thread code completely
# The old _send_email_smtp function has been moved to email_tasks.py
# and is now handled by Celery workers with proper retry logic

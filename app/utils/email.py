import smtplib
import logging
from email.message import EmailMessage
from threading import Thread
from typing import Optional
from app.core.config import settings


logger = logging.getLogger(__name__)


def _send_email_smtp(message: EmailMessage) -> None:
	try:
		with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
			server.starttls()
			if settings.SMTP_USER and settings.SMTP_PASSWORD:
				server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
			server.send_message(message)
			logger.info("Email sent to %s", message['To'])
	except Exception as e:
		logger.exception("Failed to send email: %s", e)


def send_email_async(to_email: str, subject: str, body: str, html: Optional[str] = None) -> None:
	"""Send email in background thread. Exceptions are logged but not re-raised.

	This uses SMTP settings from `app.core.config.settings`.
	"""
	msg = EmailMessage()
	msg['Subject'] = subject
	msg['From'] = f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
	msg['To'] = to_email
	msg.set_content(body)
	if html:
		msg.add_alternative(html, subtype='html')

	# Fire-and-forget in separate thread
	thread = Thread(target=_send_email_smtp, args=(msg,), daemon=True)
	thread.start()


def send_order_confirmation_email(order, to_email: Optional[str] = None) -> None:
	"""Compose and send order confirmation email. Non-blocking and safe.

	`order` is expected to have `order_number` and basic details.
	"""
	recipient = to_email or (order.user.email if hasattr(order, 'user') else None)
	if not recipient:
		logger.warning("No recipient for order confirmation: order_id=%s", getattr(order, 'id', None))
		return

	subject = f"Order Confirmation - {getattr(order, 'order_number', 'Order') }"
	body = f"Thank you for your order. Your order number is {getattr(order, 'order_number', '')}."

	try:
		send_email_async(recipient, subject, body)
	except Exception:
		logger.exception("Unexpected error while queueing order confirmation email")

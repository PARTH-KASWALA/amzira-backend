import secrets
import string

from sqlalchemy.orm import Session

from app.models.order import Order


def generate_order_number(db: Session) -> str:
    """Generate a unique AMZ-prefixed order number with bounded retries."""
    max_attempts = 10

    for _ in range(max_attempts):
        candidate = "AMZ-" + "".join(
            secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12)
        )
        existing = db.query(Order.id).filter(Order.order_number == candidate).first()
        if not existing:
            return candidate

    raise ValueError("Failed to generate unique order number")

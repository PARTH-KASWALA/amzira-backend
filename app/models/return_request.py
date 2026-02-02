from sqlalchemy import Column, String, ForeignKey, DateTime, Enum, Text, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
import uuid

from app.db.base_class import Base


class ReturnReason(str, enum.Enum):
    SIZE_ISSUE = "size_issue"
    DAMAGED = "damaged"
    WRONG_ITEM = "wrong_item"
    NOT_AS_DESCRIBED = "not_as_described"
    QUALITY_ISSUE = "quality_issue"
    OTHER = "other"


class ReturnStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PICKED_UP = "picked_up"
    REFUNDED = "refunded"


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    order_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id"),
        nullable=False,
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    reason = Column(Enum(ReturnReason), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(
        Enum(ReturnStatus),
        default=ReturnStatus.REQUESTED,
        nullable=False,
    )

    refund_amount = Column(Float, nullable=True)
    refund_method = Column(String(50), nullable=True)
    refund_transaction_id = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="returns")
    order_item = relationship("OrderItem")
    user = relationship("User")

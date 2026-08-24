from sqlalchemy import Boolean, Column, String, ForeignKey, DateTime, Enum, Index, Text, Integer, Numeric, UniqueConstraint, text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

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
    REFUND_PENDING = "refund_pending"
    REFUND_FAILED = "refund_failed"
    REFUNDED = "refunded"


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    __table_args__ = (
        UniqueConstraint("order_item_id", name="uq_return_requests_order_item_id"),
        Index(
            "uq_return_requests_refund_transaction_id_not_null",
            "refund_transaction_id",
            unique=True,
            postgresql_where=text("refund_transaction_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    reason = Column(Enum(ReturnReason), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(
        Enum(ReturnStatus),
        default=ReturnStatus.REQUESTED,
        nullable=False,
    )

    refund_amount = Column(Numeric(10, 2), nullable=True)
    refund_method = Column(String(50), nullable=True)
    refund_transaction_id = Column(String(100), nullable=True)
    refund_error = Column(String(500), nullable=True)
    refund_gateway_response = Column(Text, nullable=True)
    inventory_restocked = Column(Boolean, default=False, nullable=False)
    shiprocket_return_order_id = Column(String(100), nullable=True, index=True)
    shiprocket_return_shipment_id = Column(String(100), nullable=True, index=True)
    return_awb_code = Column(String(100), nullable=True)
    return_tracking_url = Column(String(500), nullable=True)
    return_courier_name = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="returns")
    order_item = relationship("OrderItem")
    user = relationship("User")

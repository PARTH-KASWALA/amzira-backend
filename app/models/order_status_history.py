from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Index, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("ix_order_status_history_order_id", "order_id"),)

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    
    old_status = Column(String(50), nullable=True)  # Previous status
    new_status = Column(String(50), nullable=False)  # New status
    
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Admin who changed it, null for system
    notes = Column(Text, nullable=True)  # Additional notes about the change
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="status_history")
    changer = relationship("User")  # The admin who made the change

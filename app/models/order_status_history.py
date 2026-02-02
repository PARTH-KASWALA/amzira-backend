from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    
    old_status = Column(String(50), nullable=True)  # Previous status
    new_status = Column(String(50), nullable=False)  # New status
    
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Admin who changed it, null for system
    notes = Column(Text, nullable=True)  # Additional notes about the change
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="status_history")
    changer = relationship("User")  # The admin who made the change
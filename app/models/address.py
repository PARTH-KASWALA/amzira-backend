from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base_class import Base


class Address(Base):
    __tablename__ = "addresses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    full_name = Column(String(100), nullable=False)
    phone = Column(String(15), nullable=False)
    address_line1 = Column(String(200), nullable=False)
    address_line2 = Column(String(200))
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    pincode = Column(String(10), nullable=False)
    country = Column(String(50), default="India", nullable=False)
    
    is_default = Column(Boolean, default=False)
    address_type = Column(String(20), default="home")  # home, office

    # Relationships
    user = relationship("User", back_populates="addresses")
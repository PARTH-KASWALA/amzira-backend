from pydantic import BaseModel, field_validator
from typing import Optional
import re


class AddressBase(BaseModel):
    full_name: str
    phone: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    pincode: str
    country: str = "India"
    address_type: str = "home"
    is_default: bool = False
    
    @field_validator('pincode')
    @classmethod
    def validate_pincode(cls, v):
        if not re.match(r'^\d{6}$', v):
            raise ValueError('Pincode must be 6 digits')
        return v


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    is_default: Optional[bool] = None


class AddressResponse(AddressBase):
    id: int
    user_id: int
    
    class Config:
        from_attributes = True
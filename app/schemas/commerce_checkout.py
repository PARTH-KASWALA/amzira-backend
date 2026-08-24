from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class CartAddRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    product_id: int = Field(..., gt=0)
    variant_id: Optional[int] = Field(default=None, gt=0)
    quantity: int = Field(..., ge=1, le=10)


class CartUpdateRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=10)


class CheckoutAddressCreate(BaseModel):
    user_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15)
    address_line: str = Field(..., min_length=1, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    pincode: str = Field(..., min_length=6, max_length=10)
    is_default: bool = False

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isdigit())
        if len(cleaned) != 10:
            raise ValueError("Phone must be a valid 10-digit mobile number")
        return cleaned

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isdigit())
        if len(cleaned) != 6:
            raise ValueError("Pincode must be 6 digits")
        return cleaned


class CheckoutRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    address_id: int = Field(..., gt=0)


class CreatePaymentOrderRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    address_id: int = Field(..., gt=0)
    coupon_code: Optional[str] = Field(default=None, min_length=1, max_length=50)

    @field_validator("coupon_code")
    @classmethod
    def normalize_coupon_code(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else None


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str = Field(..., min_length=1, max_length=100)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=100)
    razorpay_signature: str = Field(..., min_length=1, max_length=255)
    user_id: Optional[int] = Field(default=None, gt=0)
    address_id: Optional[int] = Field(default=None, gt=0)


class CartItemSummary(BaseModel):
    cart_item_id: int
    product_id: int
    product_name: str
    product_image: Optional[str] = None
    variant_id: Optional[int] = None
    variant_details: Optional[str] = None
    quantity: int
    unit_price: float
    total_price: float


class CartSummaryResponse(BaseModel):
    user_id: int
    items: List[CartItemSummary]
    subtotal: float
    tax: float
    total: float


class AddressSummaryResponse(BaseModel):
    id: int
    user_id: int
    name: str
    phone: str
    address_line: str
    city: str
    state: str
    pincode: str
    is_default: bool


class CheckoutPreviewResponse(BaseModel):
    user_id: int
    address_id: int
    items: List[CartItemSummary]
    subtotal: float
    tax: float
    total: float
    status: str


class OrderItemSummary(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    price: float


class OrderCreateResponse(BaseModel):
    order_id: int
    order_number: str
    user_id: int
    address_id: int
    total_amount: float
    status: str
    created_at: datetime
    items: List[OrderItemSummary]


class PaymentOrderResponse(BaseModel):
    razorpay_order_id: str
    razorpay_key_id: str
    amount: int
    currency: str
    subtotal: float
    tax: float
    total: float


class PaymentVerificationResponse(BaseModel):
    order_id: int
    order_number: str
    payment_status: str
    order_status: str


class AdminOrderSummary(BaseModel):
    order_id: int
    order_number: str
    user_id: int
    customer_name: Optional[str] = None
    total_amount: float
    status: str
    created_at: datetime

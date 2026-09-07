from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional, List
from datetime import datetime
from urllib.parse import urlparse
import bleach


class ReviewCreate(BaseModel):
    product_id: int
    rating: int = Field(..., ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = Field(None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def sanitize_comment(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return bleach.clean(value, tags=[], attributes={}, strip=True).strip()


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = Field(None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def sanitize_comment(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return bleach.clean(value, tags=[], attributes={}, strip=True).strip()


class ReviewMediaResponse(BaseModel):
    id: int
    media_url: str
    alt_text: Optional[str]
    display_order: int
    is_published: bool

    class Config:
        from_attributes = True


class MarketplaceReviewMediaImport(BaseModel):
    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    media_url: str = Field(min_length=8, max_length=500, pattern=r"^https://")
    alt_text: Optional[str] = Field(default=None, max_length=200)
    # A seller login is not enough to reuse a parent's photograph. This
    # reference is retained for the moderation/audit trail before publishing.
    consent_reference: str = Field(min_length=3, max_length=250)

    @field_validator("media_url")
    @classmethod
    def require_amzira_controlled_media(cls, value: str) -> str:
        host = (urlparse(value).hostname or "").lower()
        allowed_hosts = {"cdn.amzira.com", "api.amzira.com", "api-staging.amzira.com"}
        if host not in allowed_hosts and not host.endswith(".r2.dev") and not host.endswith(".cloudflarestorage.com"):
            raise ValueError("Customer photos must first be stored on an AMZIRA-controlled media host")
        return value


class MarketplaceReviewImport(BaseModel):
    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    product_id: int = Field(gt=0)
    marketplace: Literal["myntra", "flipkart"]
    source_review_id: str = Field(min_length=1, max_length=128)
    source_product_id: str = Field(min_length=1, max_length=128)
    source_listing_url: str = Field(min_length=8, max_length=500, pattern=r"^https://")
    reviewer_name: str = Field(min_length=1, max_length=120)
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)
    marketplace_purchase_verified: bool
    # These two confirmations are mandatory because marketplace evidence must
    # be account-owned and permitted for DTC reuse before it can be displayed.
    source_account_verified: Literal[True]
    review_reuse_authorized: Literal[True]
    publish: bool = False
    media: List[MarketplaceReviewMediaImport] = Field(default_factory=list, max_length=10)

    @field_validator("comment")
    @classmethod
    def sanitize_imported_comment(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return bleach.clean(value, tags=[], attributes={}, strip=True).strip()


class ReviewMediaModerationUpdate(BaseModel):
    """Admin decision for a customer photo already stored by AMZIRA."""

    publish: bool


class ReviewResponse(BaseModel):
    id: str
    user_id: Optional[int]
    product_id: int
    rating: int
    comment: Optional[str]
    verified_purchase: bool
    marketplace_verified_purchase: bool = False
    source: str = "amzira"
    source_listing_url: Optional[str] = None
    created_at: datetime
    user_name: str  # Full name of the reviewer
    media: List[ReviewMediaResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ReviewListResponse(BaseModel):
    reviews: List[ReviewResponse]
    total: int
    page: int
    per_page: int

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
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


class ReviewResponse(BaseModel):
    id: str
    user_id: int
    product_id: int
    rating: int
    comment: Optional[str]
    verified_purchase: bool
    created_at: datetime
    user_name: str  # Full name of the reviewer

    class Config:
        from_attributes = True


class ReviewListResponse(BaseModel):
    reviews: List[ReviewResponse]
    total: int
    page: int
    per_page: int

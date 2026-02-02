from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ReviewCreate(BaseModel):
    product_id: int
    rating: int = Field(..., ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = Field(None, max_length=1000)


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = Field(None, max_length=1000)


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
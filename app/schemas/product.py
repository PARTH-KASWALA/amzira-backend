from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime


class ProductImageResponse(BaseModel):
    id: int
    image_url: str
    alt_text: Optional[str]
    display_order: int
    is_primary: bool
    
    class Config:
        from_attributes = True


class ProductVariantResponse(BaseModel):
    id: int
    size: str
    color: Optional[str]
    sku: str
    stock_quantity: int
    additional_price: float
    is_active: bool
    measurements: Optional[Dict[str, str]] = None
    
    class Config:
        from_attributes = True


class OccasionResponse(BaseModel):
    id: int
    name: str
    slug: str
    
    class Config:
        from_attributes = True


class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    
    class Config:
        from_attributes = True


class ProductDefaultVariantResponse(BaseModel):
    variant_id: int
    size: str
    color: Optional[str]
    stock_quantity: int


class ProductListResponse(BaseModel):
    id: int
    name: str
    slug: str
    base_price: float
    sale_price: Optional[float]
    discount_percentage: int
    is_featured: bool
    is_bestseller: bool = False
    is_most_loved: bool = False
    is_new_arrival: bool = False
    collection: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    stock_quantity: int
    default_variant: Optional[ProductDefaultVariantResponse] = None
    category: CategoryResponse
    primary_image: Optional[str] = None
    in_stock: bool
    
    class Config:
        from_attributes = True


class ProductDetailResponse(ProductListResponse):
    description: Optional[str]
    fabric: Optional[str]
    lining: Optional[str] = None
    included_pieces: List[str] = Field(default_factory=list)
    age_recommendation: Optional[str] = None
    fit_note: Optional[str] = None
    care_instructions: Optional[str]
    dispatch_days_min: Optional[int] = None
    dispatch_days_max: Optional[int] = None
    is_exchange_eligible: Optional[bool] = None
    is_return_eligible: Optional[bool] = None
    return_window_hours: Optional[int] = None
    images: List[ProductImageResponse]
    variants: List[ProductVariantResponse]
    occasions: List[OccasionResponse]
    created_at: datetime
    
    class Config:
        from_attributes = True


class ProductCreate(BaseModel):
    name: str
    category_id: int
    subcategory_id: Optional[int] = None
    description: Optional[str] = None
    base_price: float
    sale_price: Optional[float] = None
    fabric: Optional[str] = None
    lining: Optional[str] = None
    included_pieces: List[str] = Field(default_factory=list)
    age_recommendation: Optional[str] = None
    fit_note: Optional[str] = None
    care_instructions: Optional[str] = None
    is_featured: bool = False
    occasion_ids: List[int] = Field(default_factory=list)

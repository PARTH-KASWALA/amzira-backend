from pydantic import BaseModel
from typing import Optional, List
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


class ProductListResponse(BaseModel):
    id: int
    name: str
    slug: str
    base_price: float
    sale_price: Optional[float]
    discount_percentage: int
    is_featured: bool
    category: CategoryResponse
    primary_image: Optional[str] = None
    in_stock: bool
    
    class Config:
        from_attributes = True


class ProductDetailResponse(ProductListResponse):
    description: Optional[str]
    fabric: Optional[str]
    care_instructions: Optional[str]
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
    care_instructions: Optional[str] = None
    is_featured: bool = False
    occasion_ids: List[int] = []
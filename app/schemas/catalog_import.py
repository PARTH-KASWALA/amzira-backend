from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class CatalogVariantImport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sku: str = Field(min_length=1, max_length=100)
    size: str = Field(min_length=1, max_length=20)
    color: str | None = Field(default=None, max_length=50)
    stock_quantity: int = Field(ge=0, le=1_000_000)
    additional_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    is_active: bool = True
    measurements: dict[str, str] | None = None

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        return value.upper()

    @field_validator("measurements")
    @classmethod
    def validate_measurements(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        allowed = {
            "choli_length", "chest", "shoulder", "choli_waist",
            "lehenga_length", "lehenga_waist", "lehenga_ghera",
        }
        invalid = set(value) - allowed
        if invalid:
            raise ValueError(f"Unsupported measurement keys: {', '.join(sorted(invalid))}")
        if not value:
            raise ValueError("measurements cannot be empty")
        normalized = {str(key): str(measurement).strip() for key, measurement in value.items()}
        if any(not measurement or len(measurement) > 40 for measurement in normalized.values()):
            raise ValueError("Each measurement must be a non-empty value up to 40 characters")
        return normalized


class CatalogImageImport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    image_url: HttpUrl
    alt_text: str | None = Field(default=None, max_length=200)
    display_order: int = Field(default=0, ge=0, le=100)
    is_primary: bool = False


class CatalogProductImport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=250, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category_slug: str = Field(min_length=1, max_length=100)
    subcategory_slug: str | None = Field(default=None, max_length=100)
    base_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    sale_price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    description: str | None = None
    fabric: str | None = Field(default=None, max_length=100)
    lining: str | None = Field(default=None, max_length=200)
    included_pieces: list[str] = Field(default_factory=list, max_length=10)
    age_recommendation: str | None = Field(default=None, max_length=100)
    fit_note: str | None = Field(default=None, max_length=1000)
    care_instructions: str | None = None
    dispatch_days_min: int | None = Field(default=None, ge=0, le=60)
    dispatch_days_max: int | None = Field(default=None, ge=0, le=60)
    is_exchange_eligible: bool | None = None
    is_return_eligible: bool | None = None
    return_window_hours: int | None = Field(default=None, ge=1, le=720)
    meta_title: str | None = Field(default=None, max_length=100)
    meta_description: str | None = Field(default=None, max_length=300)
    audience: Literal["kids_girls"] = "kids_girls"
    collection: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    status: Literal["active", "draft", "archived"] = "active"
    is_featured: bool = False
    is_bestseller: bool = False
    is_most_loved: bool = False
    is_new_arrival: bool = False
    occasion_slugs: list[str] = Field(default_factory=list, max_length=20)
    images: list[CatalogImageImport] = Field(default_factory=list, max_length=20)
    variants: list[CatalogVariantImport] = Field(min_length=1, max_length=200)
    external_source: str | None = Field(default=None, max_length=50)
    external_id: str | None = Field(default=None, max_length=100)
    style_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_prices_and_identity(self):
        if self.sale_price is not None and self.sale_price > self.base_price:
            raise ValueError("sale_price cannot exceed base_price")
        if bool(self.external_source) != bool(self.external_id):
            raise ValueError("external_source and external_id must be supplied together")
        if (self.dispatch_days_min is None) != (self.dispatch_days_max is None):
            raise ValueError("dispatch_days_min and dispatch_days_max must be supplied together")
        if self.dispatch_days_min is not None and self.dispatch_days_min > self.dispatch_days_max:
            raise ValueError("dispatch_days_min cannot exceed dispatch_days_max")
        if self.return_window_hours is not None and self.is_return_eligible is not True:
            raise ValueError("return_window_hours requires is_return_eligible=true")
        if any(not piece.strip() or len(piece.strip()) > 80 for piece in self.included_pieces):
            raise ValueError("included_pieces must contain non-empty values up to 80 characters")
        primary_count = sum(1 for image in self.images if image.is_primary)
        if primary_count > 1:
            raise ValueError("only one image can be marked primary")
        return self


class CatalogImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[CatalogProductImport] = Field(min_length=1, max_length=500)
    mode: Literal["create", "upsert"] = "create"
    dry_run: bool = False

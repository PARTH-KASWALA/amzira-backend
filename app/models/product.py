from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    literal_column,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
    func,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base_class import Base


# Many-to-many: Products ↔ Occasions
product_occasions = Table(
    'product_occasions',
    Base.metadata,
    Column('product_id', Integer, ForeignKey('products.id'), primary_key=True),
    Column('occasion_id', Integer, ForeignKey('occasions.id'), primary_key=True)
)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "external_source",
            "external_id",
            name="uq_products_external_source_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    subcategory_id = Column(Integer, ForeignKey("subcategories.id"), nullable=True)
    
    name = Column(String(200), nullable=False, index=True)
    slug = Column(String(250), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    external_source = Column(String(50), nullable=True)
    external_id = Column(String(100), nullable=True)
    style_code = Column(String(100), nullable=True, index=True)
    audience = Column(String(50), default="kids_girls", nullable=False)
    collection = Column(String(100), nullable=True)
    tags = Column(Text, nullable=True)
    
    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    sale_price = Column(Numeric(10, 2), nullable=True)
    discount_percentage = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False)
    catalog_status = Column(String(20), default="active", nullable=False)
    is_bestseller = Column(Boolean, default=False, nullable=False)
    is_new_arrival = Column(Boolean, default=False, nullable=False)
    
    # Ratings
    avg_rating = Column(Float, default=0.0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    
    # SEO & Metadata
    meta_title = Column(String(100))
    meta_description = Column(String(300))
    
    # Fabric & Care
    fabric = Column(String(100))
    care_instructions = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("Category", back_populates="products")
    subcategory = relationship("Subcategory", back_populates="products")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan")
    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    occasions = relationship("Occasion", secondary=product_occasions, back_populates="products")
    reviews = relationship("Review", back_populates="product", cascade="all, delete-orphan")
    wishlist_items = relationship("Wishlist", back_populates="product", cascade="all, delete-orphan")

    @hybrid_property
    def total_stock(self) -> int:
        return sum((variant.stock_quantity or 0) for variant in self.variants)

    @total_stock.expression
    def total_stock(cls):
        return (
            select(func.coalesce(func.sum(ProductVariant.stock_quantity), 0))
            .where(ProductVariant.product_id == cls.id)
            .correlate(cls)
            .scalar_subquery()
        )

# Composite indexes for performance
Index('ix_products_category_id', Product.category_id)
Index('idx_product_category_active', Product.category_id, Product.is_active)
Index('ix_products_category_active', Product.category_id, Product.is_active)
Index('idx_product_price_range', Product.sale_price, Product.base_price)
Index(
    'ix_products_search_tsv_gin',
    func.to_tsvector(
        literal_column("'simple'"),
        func.coalesce(Product.name, literal_column("''"))
        + literal_column("' '")
        + func.coalesce(Product.description, literal_column("''")),
    ),
    postgresql_using='gin',
)


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (Index("ix_product_images_product_id", "product_id"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String(500), nullable=False)
    alt_text = Column(String(200))
    display_order = Column(Integer, default=0)
    is_primary = Column(Boolean, default=False)

    # Relationships
    product = relationship("Product", back_populates="images")


class ProductVariant(Base):
    """Handles Size + Color + Stock per variant"""
    __tablename__ = "product_variants"
    __table_args__ = (
        CheckConstraint(
            "stock_quantity >= 0",
            name="ck_product_variants_stock_non_negative",
        ),
        Index("ix_product_variants_sku", "sku", unique=True),
        Index("ix_product_variants_product_id", "product_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    
    size = Column(String(10), nullable=False)  # S, M, L, XL, XXL, 38, 40, etc.
    color = Column(String(50), nullable=True)  # Maroon, Gold, etc.
    sku = Column(String(100), nullable=False)
    
    stock_quantity = Column(Integer, default=0, nullable=False)
    additional_price = Column(Numeric(10, 2), default=0.0)  # Extra cost for this variant
    
    is_active = Column(Boolean, default=True)

    # Relationships
    product = relationship("Product", back_populates="variants")


class Occasion(Base):
    __tablename__ = "occasions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)  # Wedding, Reception, Sangeet
    slug = Column(String(50), unique=True, nullable=False, index=True)

    # Relationships
    products = relationship("Product", secondary=product_occasions, back_populates="occasions")

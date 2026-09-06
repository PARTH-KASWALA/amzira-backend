import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.product import Product
from app.schemas.review import MarketplaceReviewImport
from app.services.review_service import ReviewService


def _product(db: Session) -> Product:
    category = Category(name="Kids", slug="kids", is_active=True)
    db.add(category)
    db.flush()
    product = Product(
        category_id=category.id,
        name="Emerald Temple Lehenga",
        slug="emerald-temple-lehenga",
        base_price=2499,
        is_active=True,
    )
    db.add(product)
    db.commit()
    return product


def test_marketplace_review_import_keeps_source_and_media_consent(db_session: Session):
    product = _product(db_session)
    payload = MarketplaceReviewImport(
        product_id=product.id,
        marketplace="myntra",
        source_review_id="MYNTRA-REVIEW-42",
        source_product_id="ETHZY-42",
        source_listing_url="https://www.myntra.com/example/ETHZY-42",
        reviewer_name="Priya",
        rating=5,
        comment="Lovely colour and fit.",
        marketplace_purchase_verified=True,
        source_account_verified=True,
        review_reuse_authorized=True,
        publish=True,
        media=[
            {
                "media_url": "https://cdn.amzira.com/reviews/42.webp",
                "alt_text": "Customer photo of an emerald lehenga",
                "consent_reference": "customer-consent-42",
            }
        ],
    )

    review = ReviewService.import_marketplace_review(db_session, payload)
    db_session.refresh(product)
    public = ReviewService.get_reviews_for_product(db_session, product.id)

    assert review.source == "myntra"
    assert review.verified_purchase is False
    assert review.marketplace_verified_purchase is True
    assert review.media[0].media_url.endswith("42.webp")
    assert product.review_count == 1
    assert public.reviews[0].user_name == "Priya"


def test_marketplace_review_import_requires_ownership_and_reuse_confirmation():
    with pytest.raises(ValidationError):
        MarketplaceReviewImport(
            product_id=1,
            marketplace="flipkart",
            source_review_id="FK-1",
            source_product_id="STYLE-1",
            source_listing_url="https://www.flipkart.com/example",
            reviewer_name="Customer",
            rating=4,
            marketplace_purchase_verified=True,
            source_account_verified=False,
            review_reuse_authorized=True,
        )

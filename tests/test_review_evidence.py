import pytest
from io import BytesIO
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.category import Category
from app.models.product import Product
from app.models.review import Review
from app.models.user import User
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
    assert review.source_listing_url == "https://www.myntra.com/example/ETHZY-42"
    assert review.media[0].media_url.endswith("42.webp")
    assert review.media[0].is_published is True
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


def test_direct_customer_photo_stays_private_until_moderated_and_can_be_withdrawn(db_session: Session, monkeypatch):
    product = _product(db_session)
    customer = User(
        email="parent@example.com",
        password_hash="not-used-in-service-test",
        full_name="Parent Customer",
    )
    db_session.add(customer)
    db_session.flush()
    review = Review(
        user_id=customer.id,
        product_id=product.id,
        rating=5,
        comment="Great fit for a temple function.",
        verified_purchase=True,
        source="amzira",
        is_published=True,
    )
    db_session.add(review)
    db_session.commit()

    deleted_urls: list[str] = []
    monkeypatch.setattr(
        "app.services.review_service.save_review_image",
        lambda _: "https://cdn.amzira.com/reviews/customer-photo.webp",
    )
    monkeypatch.setattr("app.services.review_service.delete_review_image", deleted_urls.append)
    upload = UploadFile(filename="customer-photo.webp", file=BytesIO(b"not-read-in-this-test"))

    submitted = ReviewService.add_direct_review_media(db_session, review.id, customer.id, upload)
    assert submitted.media[0].is_published is False
    assert ReviewService.get_reviews_for_product(db_session, product.id).reviews[0].media == []

    published = ReviewService.moderate_review_media(db_session, review.id, submitted.media[0].id, True)
    assert published.media[0].is_published is True
    public = ReviewService.get_reviews_for_product(db_session, product.id)
    assert public.reviews[0].media[0].media_url.endswith("customer-photo.webp")

    ReviewService.delete_direct_review_media(db_session, review.id, submitted.media[0].id, customer.id)
    assert deleted_urls == ["https://cdn.amzira.com/reviews/customer-photo.webp"]
    assert ReviewService.get_reviews_for_product(db_session, product.id).reviews[0].media == []

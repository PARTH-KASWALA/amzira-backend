from datetime import datetime, timezone

from fastapi import UploadFile
from sqlalchemy import and_, func
from sqlalchemy.orm import Session, selectinload
from fastapi import HTTPException, status

from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.review import Review, ReviewMedia
from app.models.user import User
from app.schemas.review import (
    MarketplaceReviewImport,
    ReviewCreate,
    ReviewListResponse,
    ReviewMediaResponse,
    ReviewResponse,
    ReviewUpdate,
)
from app.core.cache import invalidate_product_cache
from app.utils.image_upload import delete_review_image, save_review_image


class ReviewService:
    @staticmethod
    def _check_verified_purchase(db: Session, user_id: int, product_id: int) -> bool:
        """Check whether an AMZIRA customer purchased this product directly."""
        completed_statuses = [
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        ]
        return (
            db.query(OrderItem)
            .join(Order)
            .filter(
                and_(
                    Order.user_id == user_id,
                    OrderItem.product_id == product_id,
                    Order.status.in_(completed_statuses),
                )
            )
            .first()
            is not None
        )

    @staticmethod
    def _recalculate_product_ratings(db: Session, product_id: int):
        """Count only public reviews; moderated imports stay out of ratings."""
        result = (
            db.query(
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).label("review_count"),
            )
            .filter(Review.product_id == product_id, Review.is_published == True)
            .first()
        )
        db.query(Product).filter(Product.id == product_id).update(
            {
                "avg_rating": float(result.avg_rating) if result.avg_rating else 0.0,
                "review_count": result.review_count or 0,
            }
        )

    @staticmethod
    def _to_response(
        review: Review,
        user_name: str | None = None,
        *,
        include_unpublished_media: bool = False,
    ) -> ReviewResponse:
        return ReviewResponse(
            id=review.id,
            user_id=review.user_id,
            product_id=review.product_id,
            rating=review.rating,
            comment=review.comment,
            verified_purchase=review.verified_purchase,
            marketplace_verified_purchase=review.marketplace_verified_purchase,
            source=review.source,
            created_at=review.created_at,
            user_name=review.reviewer_name or user_name or "AMZIRA customer",
            media=[
                ReviewMediaResponse(
                    id=media.id,
                    media_url=media.media_url,
                    alt_text=media.alt_text,
                    display_order=media.display_order,
                    is_published=media.is_published,
                )
                for media in sorted(review.media, key=lambda item: (item.display_order, item.id))
                if include_unpublished_media or media.is_published
            ],
        )

    @staticmethod
    def create_review(db: Session, user_id: int, review_data: ReviewCreate) -> ReviewResponse:
        """Create one verified, direct AMZIRA-purchase review per customer."""
        existing = (
            db.query(Review)
            .filter(and_(Review.user_id == user_id, Review.product_id == review_data.product_id))
            .first()
        )
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have already reviewed this product")
        if not ReviewService._check_verified_purchase(db, user_id, review_data.product_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only review products you have purchased")

        review = Review(
            user_id=user_id,
            product_id=review_data.product_id,
            rating=review_data.rating,
            comment=review_data.comment,
            verified_purchase=True,
            marketplace_verified_purchase=False,
            source="amzira",
            is_published=True,
        )
        db.add(review)
        db.flush()
        ReviewService._recalculate_product_ratings(db, review_data.product_id)
        db.commit()
        db.refresh(review)
        invalidate_product_cache()
        user = db.query(User).filter(User.id == user_id).first()
        return ReviewService._to_response(review, user.full_name if user else None)

    @staticmethod
    def import_marketplace_review(db: Session, review_data: MarketplaceReviewImport) -> ReviewResponse:
        """Import a moderated, rights-cleared review from a verified seller account.

        Marketplace purchases stay explicitly distinct from AMZIRA verified
        purchases. Photo records are created only when a consent reference is
        supplied with each asset; imports are private unless publish=true.
        """
        product = db.query(Product).filter(Product.id == review_data.product_id).first()
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

        review = (
            db.query(Review)
            .options(selectinload(Review.media))
            .filter(
                Review.source == review_data.marketplace,
                Review.source_review_id == review_data.source_review_id,
            )
            .first()
        )
        if review and review.product_id != product.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Marketplace review identity is already mapped to another product",
            )
        if review is None:
            review = Review(
                product_id=product.id,
                source=review_data.marketplace,
                source_review_id=review_data.source_review_id,
                verified_purchase=False,
            )
            db.add(review)

        review.rating = review_data.rating
        review.comment = review_data.comment
        review.marketplace_verified_purchase = review_data.marketplace_purchase_verified
        review.source_product_id = review_data.source_product_id
        review.source_listing_url = review_data.source_listing_url
        review.reviewer_name = review_data.reviewer_name
        review.is_published = review_data.publish
        review.verified_purchase = False
        review.media.clear()
        for display_order, media in enumerate(review_data.media):
            review.media.append(
                ReviewMedia(
                    media_url=media.media_url,
                    alt_text=media.alt_text,
                    consent_reference=media.consent_reference,
                    display_order=display_order,
                    is_published=review_data.publish,
                )
            )

        db.flush()
        ReviewService._recalculate_product_ratings(db, product.id)
        db.commit()
        db.refresh(review)
        invalidate_product_cache()
        return ReviewService._to_response(review)

    @staticmethod
    def add_direct_review_media(
        db: Session,
        review_id: str,
        user_id: int,
        image: UploadFile,
    ) -> ReviewResponse:
        """Accept a verified purchaser's photo and hold it for moderation."""
        review = (
            db.query(Review)
            .options(selectinload(Review.media))
            .filter(Review.id == review_id)
            .first()
        )
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        if review.source != "amzira" or review.user_id != user_id or not review.verified_purchase:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only add photos to your verified AMZIRA review",
            )
        if len(review.media) >= 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A review can include up to three customer photos",
            )

        image_url = save_review_image(image)
        review.media.append(
            ReviewMedia(
                media_url=image_url,
                consent_reference=f"direct-review-upload:{review.id}:{datetime.now(timezone.utc).isoformat()}",
                display_order=len(review.media),
                is_published=False,
            )
        )
        db.commit()
        db.refresh(review)
        user = db.query(User).filter(User.id == user_id).first()
        return ReviewService._to_response(
            review,
            user.full_name if user else None,
            include_unpublished_media=True,
        )

    @staticmethod
    def moderate_review_media(db: Session, review_id: str, media_id: int, publish: bool) -> ReviewResponse:
        """Publish or hide one customer photo without changing its review."""
        review = (
            db.query(Review)
            .options(selectinload(Review.media))
            .filter(Review.id == review_id)
            .first()
        )
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        media = next((item for item in review.media if item.id == media_id), None)
        if not media:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review photo not found")
        media.is_published = publish
        db.commit()
        db.refresh(review)
        return ReviewService._to_response(review, include_unpublished_media=True)

    @staticmethod
    def delete_direct_review_media(db: Session, review_id: str, media_id: int, user_id: int) -> None:
        """Let a reviewer withdraw their own customer photo and its consent."""
        review = (
            db.query(Review)
            .options(selectinload(Review.media))
            .filter(Review.id == review_id)
            .first()
        )
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        if review.source != "amzira" or review.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only withdraw photos from your own review",
            )
        media = next((item for item in review.media if item.id == media_id), None)
        if not media:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review photo not found")
        image_url = media.media_url
        db.delete(media)
        db.commit()
        delete_review_image(image_url)

    @staticmethod
    def get_reviews_for_product(
        db: Session,
        product_id: int,
        page: int = 1,
        per_page: int = 10,
        rating: int | None = None,
    ) -> ReviewListResponse:
        """Return public reviews only, with a source and permitted customer media."""
        offset = (page - 1) * per_page
        query = (
            db.query(Review, User.full_name.label("user_name"))
            .outerjoin(User, Review.user_id == User.id)
            .options(selectinload(Review.media))
            .filter(Review.product_id == product_id, Review.is_published == True)
            .order_by(Review.created_at.desc())
        )
        if rating is not None:
            query = query.filter(Review.rating == rating)
        total = query.count()
        reviews = query.offset(offset).limit(per_page).all()
        return ReviewListResponse(
            reviews=[ReviewService._to_response(row.Review, row.user_name) for row in reviews],
            total=total,
            page=page,
            per_page=per_page,
        )

    @staticmethod
    def update_review(
        db: Session,
        review_id: str,
        user_id: int,
        user_role: str,
        review_data: ReviewUpdate,
    ) -> ReviewResponse:
        """Update a direct review as its owner, or any review as an admin."""
        review = db.query(Review).options(selectinload(Review.media)).filter(Review.id == review_id).first()
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        if review.user_id != user_id and user_role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own reviews")
        if review_data.rating is not None:
            review.rating = review_data.rating
        if review_data.comment is not None:
            review.comment = review_data.comment
        ReviewService._recalculate_product_ratings(db, review.product_id)
        db.commit()
        db.refresh(review)
        invalidate_product_cache()
        user = db.query(User).filter(User.id == review.user_id).first() if review.user_id else None
        return ReviewService._to_response(review, user.full_name if user else None)

    @staticmethod
    def delete_review(db: Session, review_id: str, user_id: int, user_role: str):
        """Delete a direct review as its owner, or any review as an admin."""
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
        if review.user_id != user_id and user_role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own reviews")
        product_id = review.product_id
        db.delete(review)
        db.flush()
        ReviewService._recalculate_product_ratings(db, product_id)
        db.commit()
        invalidate_product_cache()

from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import List

from app.models.wishlist import Wishlist
from app.models.product import Product, ProductImage
from app.schemas.wishlist import WishlistCreate, WishlistResponse, WishlistListResponse
from app.utils.response import success, error


class WishlistService:
    
    @staticmethod
    def add_to_wishlist(db: Session, user_id: int, wishlist_data: WishlistCreate) -> WishlistResponse:
        """Add a product to user's wishlist. Enforces one product per user."""
        # Check if product exists
        product = db.query(Product).filter(Product.id == wishlist_data.product_id).first()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )
        
        # Check if already in wishlist
        existing = db.query(Wishlist).filter(
            and_(Wishlist.user_id == user_id, Wishlist.product_id == wishlist_data.product_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product is already in your wishlist"
            )
        
        # Create wishlist item
        wishlist_item = Wishlist(
            user_id=user_id,
            product_id=wishlist_data.product_id
        )
        
        db.add(wishlist_item)
        db.commit()
        db.refresh(wishlist_item)
        
        # Get product details
        primary_image = db.query(ProductImage).filter(
            and_(ProductImage.product_id == product.id, ProductImage.is_primary == True)
        ).first()
        
        return WishlistResponse(
            id=wishlist_item.id,
            user_id=wishlist_item.user_id,
            product_id=wishlist_item.product_id,
            created_at=wishlist_item.created_at,
            product_name=product.name,
            product_slug=product.slug,
            product_price=product.sale_price or product.base_price,
            product_image=primary_image.image_url if primary_image else None
        )
    
    @staticmethod
    def remove_from_wishlist(db: Session, user_id: int, product_id: int):
        """Remove a product from user's wishlist."""
        wishlist_item = db.query(Wishlist).filter(
            and_(Wishlist.user_id == user_id, Wishlist.product_id == product_id)
        ).first()
        
        if not wishlist_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found in your wishlist"
            )
        
        db.delete(wishlist_item)
        db.commit()
    
    @staticmethod
    def get_user_wishlist(db: Session, user_id: int) -> WishlistListResponse:
        """Get all items in user's wishlist with product details."""
        wishlist_items = db.query(Wishlist, Product, ProductImage).outerjoin(
            Product, Wishlist.product_id == Product.id
        ).outerjoin(
            ProductImage, and_(ProductImage.product_id == Product.id, ProductImage.is_primary == True)
        ).filter(Wishlist.user_id == user_id).all()
        
        wishlist_responses = []
        for wishlist_item, product, image in wishlist_items:
            wishlist_responses.append(WishlistResponse(
                id=wishlist_item.id,
                user_id=wishlist_item.user_id,
                product_id=wishlist_item.product_id,
                created_at=wishlist_item.created_at,
                product_name=product.name,
                product_slug=product.slug,
                product_price=product.sale_price or product.base_price,
                product_image=image.image_url if image else None
            ))
        
        return WishlistListResponse(
            wishlist_items=wishlist_responses,
            total=len(wishlist_responses)
        )
    
    @staticmethod
    def check_in_wishlist(db: Session, user_id: int, product_id: int) -> bool:
        """Check if a product is in user's wishlist."""
        return db.query(Wishlist).filter(
            and_(Wishlist.user_id == user_id, Wishlist.product_id == product_id)
        ).first() is not None
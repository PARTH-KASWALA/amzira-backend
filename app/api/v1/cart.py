from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.cart import CartItem
from app.models.product import Product, ProductVariant
from app.schemas.cart import CartItemCreate, CartItemUpdate, CartResponse
from app.core.exceptions import ProductNotFound, InsufficientStock

router = APIRouter()


@router.get("/", response_model=dict)
def get_cart(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's cart"""
    cart_items = db.query(CartItem).filter(CartItem.user_id == current_user.id).all()
    
    items_response = []
    subtotal = 0.0
    
    for item in cart_items:
        product = item.product
        variant = item.variant
        
        # Get primary image
        primary_image = next((img.image_url for img in product.images if img.is_primary), None)
        if not primary_image and product.images:
            primary_image = product.images[0].image_url
        
        # Calculate current price
        current_price = product.sale_price if product.sale_price else product.base_price
        current_price += variant.additional_price
        
        total_price = current_price * item.quantity
        subtotal += total_price
        
        variant_details = f"Size: {variant.size}"
        if variant.color:
            variant_details += f", Color: {variant.color}"
        
        items_response.append({
            "id": item.id,
            "product_id": product.id,
            "product_name": product.name,
            "product_slug": product.slug,
            "product_image": primary_image,
            "variant_id": variant.id,
            "variant_details": variant_details,
            "quantity": item.quantity,
            "unit_price": current_price,
            "total_price": total_price,
            "stock_available": variant.stock_quantity
        })
    
    return {
        "items": items_response,
        "subtotal": subtotal,
        "total_items": len(cart_items)
    }


@router.post("/items", status_code=status.HTTP_201_CREATED)
def add_to_cart(
    cart_item: CartItemCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Add item to cart"""
    # Verify product exists
    product = db.query(Product).filter(
        Product.id == cart_item.product_id,
        Product.is_active == True
    ).first()
    
    if not product:
        raise ProductNotFound()
    
    # Verify variant exists
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == cart_item.variant_id,
        ProductVariant.product_id == cart_item.product_id,
        ProductVariant.is_active == True
    ).first()
    
    if not variant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product variant not found"
        )
    
    # Check stock
    if variant.stock_quantity < cart_item.quantity:
        raise InsufficientStock(variant.stock_quantity)
    
    # Check if item already in cart
    existing_item = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.product_id == cart_item.product_id,
        CartItem.variant_id == cart_item.variant_id
    ).first()
    
    if existing_item:
        # Update quantity
        new_quantity = existing_item.quantity + cart_item.quantity
        if variant.stock_quantity < new_quantity:
            raise InsufficientStock(variant.stock_quantity)
        
        existing_item.quantity = new_quantity
        db.commit()
        db.refresh(existing_item)
        
        return {"message": "Cart updated", "cart_item_id": existing_item.id}
    
    # Calculate price
    price = product.sale_price if product.sale_price else product.base_price
    price += variant.additional_price
    
    # Add new item
    new_cart_item = CartItem(
        user_id=current_user.id,
        product_id=cart_item.product_id,
        variant_id=cart_item.variant_id,
        quantity=cart_item.quantity,
        price_at_addition=price
    )
    
    db.add(new_cart_item)
    db.commit()
    db.refresh(new_cart_item)
    
    return {"message": "Item added to cart", "cart_item_id": new_cart_item.id}


@router.put("/items/{item_id}")
def update_cart_item(
    item_id: int,
    update_data: CartItemUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update cart item quantity"""
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.user_id == current_user.id
    ).first()
    
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found"
        )
    
    # Check stock
    variant = cart_item.variant
    if variant.stock_quantity < update_data.quantity:
        raise InsufficientStock(variant.stock_quantity)
    
    cart_item.quantity = update_data.quantity
    db.commit()
    
    return {"message": "Cart item updated"}


@router.delete("/items/{item_id}")
def remove_from_cart(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Remove item from cart"""
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.user_id == current_user.id
    ).first()
    
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found"
        )
    
    db.delete(cart_item)
    db.commit()
    
    return {"message": "Item removed from cart"}


@router.delete("/")
def clear_cart(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Clear entire cart"""
    db.query(CartItem).filter(CartItem.user_id == current_user.id).delete()
    db.commit()
    
    return {"message": "Cart cleared"}
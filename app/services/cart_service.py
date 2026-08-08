import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import InsufficientStock, ProductNotFound
from app.core.pricing import calculate_shipping, calculate_tax, money_float
from app.models.cart import CartItem
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.schemas.cart import CartItemCreate, CartItemUpdate

logger = logging.getLogger(__name__)


def _variant_additional_price(variant: ProductVariant | None) -> float:
    return float(getattr(variant, "additional_price", 0.0) or 0.0)


def _base_cart_query(db: Session):
    return db.query(CartItem).options(
        joinedload(CartItem.product).joinedload(Product.images),
        joinedload(CartItem.variant),
    )


def _serialize_cart_items(cart_items: list[CartItem], *, user_id: int) -> dict:
    items_response = []
    subtotal = 0.0

    for item in cart_items:
        product = item.product
        variant = item.variant
        if not product or not variant:
            logger.warning(
                "cart_item_missing_relation",
                extra={
                    "user_id": user_id,
                    "cart_item_id": item.id,
                    "product_id": item.product_id,
                    "variant_id": item.variant_id,
                },
            )
            continue

        primary_image = next((img.image_url for img in product.images if img.is_primary), None)
        if not primary_image and product.images:
            primary_image = product.images[0].image_url

        current_price = float(product.sale_price if product.sale_price else product.base_price)
        current_price += _variant_additional_price(variant)

        total_price = current_price * item.quantity
        subtotal += total_price

        variant_details = f"Size: {variant.size}"
        if variant.color:
            variant_details += f", Color: {variant.color}"

        items_response.append(
            {
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
                "stock_available": variant.stock_quantity,
            }
        )

    shipping = money_float(calculate_shipping(subtotal))
    tax = money_float(calculate_tax(subtotal + shipping))
    total = money_float(subtotal + shipping + tax)

    return {
        "items": items_response,
        "subtotal": money_float(subtotal),
        "shipping": shipping,
        "shipping_amount": shipping,
        "tax": tax,
        "total": total,
        "total_items": len(items_response),
    }


def get_cart(db: Session, *, user_id: int) -> dict:
    cart_items = (
        _base_cart_query(db)
        .filter(CartItem.user_id == user_id)
        .order_by(CartItem.id.asc())
        .all()
    )
    return _serialize_cart_items(cart_items, user_id=user_id)


def add_item(db: Session, *, current_user: User, cart_item: CartItemCreate) -> CartItem:
    if cart_item.variant_id is None or cart_item.variant_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="variant_id is required and must be selected",
        )

    product = db.query(Product).filter(
        Product.id == cart_item.product_id,
        Product.is_active == True,
    ).first()
    if not product:
        raise ProductNotFound()

    variant = db.query(ProductVariant).filter(
        ProductVariant.id == cart_item.variant_id,
        ProductVariant.product_id == cart_item.product_id,
        ProductVariant.is_active == True,
    ).first()
    if not variant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product variant not found",
        )

    if variant.stock_quantity < cart_item.quantity:
        raise InsufficientStock(variant.stock_quantity)

    existing_item = db.query(CartItem).filter(
        CartItem.user_id == current_user.id,
        CartItem.product_id == cart_item.product_id,
        CartItem.variant_id == cart_item.variant_id,
    ).first()

    if existing_item:
        new_quantity = existing_item.quantity + cart_item.quantity
        if variant.stock_quantity < new_quantity:
            raise InsufficientStock(variant.stock_quantity)
        existing_item.quantity = new_quantity
        db.commit()
        db.refresh(existing_item)
        return existing_item

    price = float(product.sale_price if product.sale_price else product.base_price)
    price += _variant_additional_price(variant)

    new_cart_item = CartItem(
        user_id=current_user.id,
        product_id=cart_item.product_id,
        variant_id=cart_item.variant_id,
        quantity=cart_item.quantity,
        price_at_addition=price,
    )
    db.add(new_cart_item)
    db.commit()
    db.refresh(new_cart_item)
    return new_cart_item


def update_item(db: Session, *, current_user: User, item_id: int, update_data: CartItemUpdate) -> CartItem:
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.user_id == current_user.id,
    ).first()
    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")

    variant = cart_item.variant
    if variant.stock_quantity < update_data.quantity:
        raise InsufficientStock(variant.stock_quantity)

    cart_item.quantity = update_data.quantity
    db.commit()
    db.refresh(cart_item)
    return cart_item


def remove_item(db: Session, *, current_user: User, item_id: int) -> None:
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.user_id == current_user.id,
    ).first()
    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    db.delete(cart_item)
    db.commit()


def clear_cart(db: Session, *, user_id: int) -> None:
    db.query(CartItem).filter(CartItem.user_id == user_id).delete()
    db.commit()

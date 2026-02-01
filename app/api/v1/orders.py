from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.db.session import get_db
from app.api.deps import get_current_active_user, require_admin
from app.models.user import User
from app.models.order import Order, OrderItem, OrderStatus
from app.models.cart import CartItem
from app.models.address import Address
from app.schemas.order import OrderCreate, OrderResponse
from app.core.exceptions import OrderNotFound
import random
import string
from app.models.product import ProductVariant


router = APIRouter()


def generate_order_number() -> str:
    """Generate unique order number"""
    timestamp = datetime.now().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"AMZ{timestamp}{random_part}"


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create order from cart"""
    # Get cart items
    cart_items = db.query(CartItem).filter(CartItem.user_id == current_user.id).all()
    
    if not cart_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cart is empty"
        )
    
    # Verify addresses belong to user
    shipping_address = db.query(Address).filter(
        Address.id == order_data.shipping_address_id,
        Address.user_id == current_user.id
    ).first()
    
    billing_address = db.query(Address).filter(
        Address.id == order_data.billing_address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not shipping_address or not billing_address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )
    
    # Calculate totals
    subtotal = 0.0
    order_items_data = []
    
    for cart_item in cart_items:
        variant = cart_item.variant
        product = cart_item.product
        
        # Check stock
        if variant.stock_quantity < cart_item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for {product.name}"
            )
        
        # Calculate price
        unit_price = product.sale_price if product.sale_price else product.base_price
        unit_price += variant.additional_price
        total_price = unit_price * cart_item.quantity
        
        subtotal += total_price
        
        variant_details = f"Size: {variant.size}"
        if variant.color:
            variant_details += f", Color: {variant.color}"
        
        order_items_data.append({
            "product_id": product.id,
            "variant_id": variant.id,
            "product_name": product.name,
            "variant_details": variant_details,
            "quantity": cart_item.quantity,
            "unit_price": unit_price,
            "total_price": total_price
        })
    
    # Calculate tax (18% GST for India)
    tax_amount = subtotal * 0.18
    
    # Shipping (free for orders > 2000)
    shipping_charge = 0.0 if subtotal > 2000 else 100.0
    
    total_amount = subtotal + tax_amount + shipping_charge
    
    # Create order
    order = Order(
        order_number=generate_order_number(),
        user_id=current_user.id,
        subtotal=subtotal,
        tax_amount=tax_amount,
        shipping_charge=shipping_charge,
        total_amount=total_amount,
        status=OrderStatus.PENDING,
        shipping_address_id=order_data.shipping_address_id,
        billing_address_id=order_data.billing_address_id,
        customer_notes=order_data.customer_notes
    )
    
    db.add(order)
    db.flush()  # Get order ID
    
    # Create order items and reduce stock
    for item_data in order_items_data:
        order_item = OrderItem(
            order_id=order.id,
            **item_data
        )
        db.add(order_item)
        
        # Reduce stock
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == item_data["variant_id"]
        ).first()
        variant.stock_quantity -= item_data["quantity"]
    
    # Clear cart
    db.query(CartItem).filter(CartItem.user_id == current_user.id).delete()
    
    db.commit()
    db.refresh(order)
    
    # Format response
    items_response = [
        {
            "id": item.id,
            "product_name": item.product_name,
            "variant_details": item.variant_details,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "total_price": item.total_price
        }
        for item in order.items
    ]
    
    return {
        "order_number": order.order_number,
        "id": order.id,
        "status": order.status.value,
        "subtotal": order.subtotal,
        "tax_amount": order.tax_amount,
        "shipping_charge": order.shipping_charge,
        "total_amount": order.total_amount,
        "items": items_response,
        "created_at": order.created_at
    }


@router.get("/", response_model=List[dict])
def get_user_orders(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's order history"""
    orders = db.query(Order).filter(
        Order.user_id == current_user.id
    ).order_by(Order.created_at.desc()).all()
    
    orders_response = []
    for order in orders:
        items_response = [
            {
                "id": item.id,
                "product_name": item.product_name,
                "variant_details": item.variant_details,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price
            }
            for item in order.items
        ]
        
        orders_response.append({
            "id": order.id,
            "order_number": order.order_number,
            "status": order.status.value,
            "subtotal": order.subtotal,
            "tax_amount": order.tax_amount,
            "shipping_charge": order.shipping_charge,
            "total_amount": order.total_amount,
            "items": items_response,
            "created_at": order.created_at,
            "tracking_number": order.tracking_number
        })
    
    return orders_response


@router.get("/{order_number}", response_model=dict)
def get_order_detail(
    order_number: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get order details"""
    order = db.query(Order).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise OrderNotFound()
    
    items_response = [
        {
            "id": item.id,
            "product_name": item.product_name,
            "variant_details": item.variant_details,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "total_price": item.total_price
        }
        for item in order.items
    ]
    
    return {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status.value,
        "subtotal": order.subtotal,
        "tax_amount": order.tax_amount,
        "shipping_charge": order.shipping_charge,
        "total_amount": order.total_amount,
        "items": items_response,
        "shipping_address": {
            "full_name": order.shipping_address.full_name,
            "phone": order.shipping_address.phone,
            "address_line1": order.shipping_address.address_line1,
            "address_line2": order.shipping_address.address_line2,
            "city": order.shipping_address.city,
            "state": order.shipping_address.state,
            "pincode": order.shipping_address.pincode
        },
        "created_at": order.created_at,
        "tracking_number": order.tracking_number,
        "customer_notes": order.customer_notes
    }


@router.put("/{order_id}/cancel")
def cancel_order(
    order_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Cancel order"""
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise OrderNotFound()
    
    # Can only cancel if not shipped
    if order.status in [OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel shipped/delivered orders"
        )
    
    order.status = OrderStatus.CANCELLED
    
    # Restore stock
    for item in order.items:
        variant = item.variant
        variant.stock_quantity += item.quantity
    
    db.commit()
    
    return {"message": "Order cancelled successfully"}
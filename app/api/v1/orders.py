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
from app.services.order_tracking_service import OrderTrackingService
from app.schemas.order_tracking import OrderStatusUpdate, OrderTrackingResponse
from app.utils.response import success


router = APIRouter()


def generate_order_number(db: Session) -> str:
    """Generate a unique order number with bounded retries."""
    max_attempts = 10

    for _ in range(max_attempts):
        timestamp = datetime.now().strftime("%Y%m%d")
        random_part = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        order_number = f"AMZ{timestamp}{random_part}"

        existing = db.query(Order).filter(Order.order_number == order_number).first()
        if not existing:
            return order_number

    raise ValueError("Failed to generate unique order number")


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: OrderCreate,
    payment_method: str = "razorpay",
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
    
    # Lock variants in deterministic order to avoid deadlocks and race conditions.
    requested_quantities = {}
    for cart_item in cart_items:
        requested_quantities[cart_item.variant_id] = (
            requested_quantities.get(cart_item.variant_id, 0) + cart_item.quantity
        )

    locked_variants = {}
    for variant_id in sorted(requested_quantities.keys()):
        locked_variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == variant_id)
            .with_for_update()
            .first()
        )
        if not locked_variant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product variant {variant_id} not found",
            )
        locked_variants[variant_id] = locked_variant

    # Calculate totals
    subtotal = 0.0
    order_items_data = []
    
    for cart_item in cart_items:
        variant = locked_variants[cart_item.variant_id]
        product = cart_item.product
        
        # Check stock
        if variant.stock_quantity < requested_quantities[cart_item.variant_id]:
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
    try:
        order_number = generate_order_number(db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate order number",
        ) from exc

    order = Order(
        order_number=order_number,
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

    # Deduct reserved stock while locks are still held in this transaction.
    for variant_id, quantity in requested_quantities.items():
        locked_variants[variant_id].stock_quantity -= quantity
    
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
    
    # Handle payment method
    if (payment_method or "").lower() == "cod":
        from app.services.payment_service import create_cod_payment
        from app.utils.email import send_order_confirmation_email

        payment = create_cod_payment(order, db)
        try:
            send_order_confirmation_email(order)
        except Exception:
            # email failures should not block the response
            pass

        return success(
            message="Order placed successfully. Pay on delivery.",
            data={
                "order_number": order.order_number,
                "payment_method": "cod",
            },
        )

    # # Default: razorpay - return order info for frontend to initiate payment
    # return {
    #     "order_id": order.id,
    #     "order_number": order.order_number,
    #     "payment_method": "razorpay",
    #     "message": "Proceed to payment"
    # }


    return success(
    message="Order created successfully",
    data={
        "order_id": order.id,
        "order_number": order.order_number,
        "status": order.status,
    }
)

@router.get("/", response_model=dict)
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
    
    return success(data=orders_response, message="Orders retrieved")


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
    
    return success(
        data={
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
                "pincode": order.shipping_address.pincode,
            },
            "created_at": order.created_at,
            "tracking_number": order.tracking_number,
            "customer_notes": order.customer_notes,
        },
        message="Order detail retrieved",
    )


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
    
    previous_status = order.status
    order.status = OrderStatus.CANCELLED

    # Restore stock for states where inventory is reserved.
    if previous_status in {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}:
        for item in order.items:
            variant = item.variant
            variant.stock_quantity += item.quantity

    db.commit()
    return success(message="Order cancelled successfully")


# Order Tracking Endpoints

@router.put("/{order_id}/status", response_model=dict)
def update_order_status(
    order_id: int,
    status_update: OrderStatusUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Update order status (admin only)."""
    order = OrderTrackingService.update_order_status(
        db, order_id, status_update, current_user.id
    )
    return success(
        data={"order_id": order.id, "status": order.status.value},
        message="Order status updated",
    )


@router.get("/{order_id}/tracking", response_model=dict)
def get_order_tracking(
    order_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get order tracking information."""
    tracking = OrderTrackingService.get_order_tracking(
        db, order_id, current_user.id, current_user.role.value
    )
    return success(data=tracking.dict(), message="Order tracking retrieved")


@router.get("/my/tracking", response_model=dict)
def get_user_orders_tracking(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get tracking information for all user's orders."""
    tracking_list = OrderTrackingService.get_user_orders_tracking(db, current_user.id)
    return success(
        data=[t.dict() for t in tracking_list],
        message="Orders tracking retrieved",
    )
    




from fastapi.responses import StreamingResponse
from app.utils.invoice_generator import generate_gst_invoice


@router.get("/orders/{order_number}/invoice")
def download_invoice(
    order_number: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    order = (
        db.query(Order)
        .filter(
            Order.order_number == order_number,
            Order.user_id == current_user.id,
        )
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status == OrderStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Invoice not available for pending orders",
        )

    pdf_buffer = generate_gst_invoice(order)

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=invoice-{order_number}.pdf"
        },
    )

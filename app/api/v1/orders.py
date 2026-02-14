from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
from app.db.session import get_db
from app.api.deps import get_current_active_user, require_admin
from app.models.user import User
from app.models.order import Order, OrderItem, OrderStatus
from app.models.cart import CartItem
from app.models.address import Address
from app.schemas.order import OrderCreate
from app.core.exceptions import OrderNotFound
import random
import string
from app.models.product import ProductVariant
from app.services.order_tracking_service import OrderTrackingService
from app.schemas.order_tracking import OrderStatusUpdate, OrderTrackingResponse
from app.utils.response import success
from app.core.rate_limiter import limiter


router = APIRouter()
GST_RATE = 0.18
FREE_SHIPPING_THRESHOLD = 2000.0
DEFAULT_SHIPPING_CHARGE = 100.0


def _isoformat_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return f"{value.isoformat()}Z"


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


@router.post(
    "/",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create new order",
    description="""
Creates an order from the authenticated user's cart.

Process:
1. Validates cart is not empty
2. Verifies shipping and billing addresses belong to user
3. Locks variants to prevent overselling
4. Calculates subtotal, tax, and shipping
5. Creates order and order items
6. Reserves stock and clears cart
7. For COD, confirms payment immediately
""",
    responses={
        201: {"description": "Order created successfully"},
        400: {"description": "Cart empty or insufficient stock"},
        401: {"description": "Authentication required"},
        404: {"description": "Address not found"},
        500: {"description": "Failed to generate order number"},
    },
    tags=["Orders"],
)
@limiter.limit("10/minute")
def create_order(
    request: Request,
    order_data: OrderCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create order from cart"""
    try:
        existing_order = (
            db.query(Order)
            .filter(
                Order.user_id == current_user.id,
                Order.idempotency_key == order_data.idempotency_key,
            )
            .first()
        )
        if existing_order:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=success(
                    data={
                        "order_id": existing_order.id,
                        "order_number": existing_order.order_number,
                        "total_amount": existing_order.total_amount,
                        "status": existing_order.status.value,
                    },
                    message="Order already exists",
                ),
            )

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

        # Lock variants in deterministic order only for stock validation at order creation.
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

        subtotal = 0.0
        order_items_data = []

        for variant_id, requested_qty in requested_quantities.items():
            variant = locked_variants[variant_id]
            if variant.stock_quantity < requested_qty:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for {variant.product.name}"
                )

        for variant_id, requested_qty in requested_quantities.items():
            locked_variants[variant_id].stock_quantity -= requested_qty

        for cart_item in cart_items:
            variant = locked_variants[cart_item.variant_id]
            product = cart_item.product
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

        tax_amount = subtotal * GST_RATE
        shipping_charge = 0.0 if subtotal > FREE_SHIPPING_THRESHOLD else DEFAULT_SHIPPING_CHARGE
        total_amount = subtotal + tax_amount + shipping_charge

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
            expires_at=datetime.utcnow() + timedelta(minutes=30),
            shipping_address_id=order_data.shipping_address_id,
            billing_address_id=order_data.billing_address_id,
            customer_notes=order_data.customer_notes,
            stock_deducted=True,
            idempotency_key=order_data.idempotency_key,
        )

        db.add(order)
        db.flush()

        for item_data in order_items_data:
            db.add(OrderItem(order_id=order.id, **item_data))

        db.query(CartItem).filter(CartItem.user_id == current_user.id).delete()
        db.commit()
        db.refresh(order)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    
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
    
    payment_method = (order_data.payment_method or "razorpay").lower()
    if payment_method == "cod":
        from app.services.payment_service import create_cod_payment

        create_cod_payment(order, db)

        return success(
            message="Order placed successfully. Pay on delivery.",
            data={
                "order_number": order.order_number,
                "payment_method": "cod",
                "expires_at": _isoformat_or_none(order.expires_at),
            },
        )

    return success(
    message="Order created successfully",
    data={
        "order_id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "expires_at": _isoformat_or_none(order.expires_at),
    }
)

@router.get("/", response_model=dict)
@limiter.limit("30/minute")
def get_user_orders(
    request: Request,
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
@limiter.limit("30/minute")
def get_order_detail(
    request: Request,
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
            "expires_at": _isoformat_or_none(order.expires_at),
            "tracking_number": order.tracking_number,
            "customer_notes": order.customer_notes,
        },
        message="Order detail retrieved",
    )


@router.put("/{order_id}/cancel")
@limiter.limit("10/minute")
def cancel_order(
    request: Request,
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

    # Restore stock only if this order already deducted inventory.
    if order.stock_deducted and previous_status in {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}:
        for item in order.items:
            variant = item.variant
            variant.stock_quantity += item.quantity
        order.stock_deducted = False

    order.expires_at = None

    db.commit()
    return success(message="Order cancelled successfully")


# Order Tracking Endpoints

@router.put("/{order_id}/status", response_model=dict)
@limiter.limit("20/minute")
def update_order_status(
    request: Request,
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
@limiter.limit("30/minute")
def get_order_tracking(
    request: Request,
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
@limiter.limit("30/minute")
def get_user_orders_tracking(
    request: Request,
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
@limiter.limit("20/minute")
def download_invoice(
    request: Request,
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

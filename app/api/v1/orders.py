from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload, selectinload
from typing import Optional
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from app.db.session import get_db
from app.api.deps import get_current_active_user, get_current_user_optional, require_admin
from app.models.user import User
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.cart import CartItem
from app.models.address import Address
from app.models.return_request import ReturnRequest, ReturnReason
from app.schemas.order import OrderCreate
from app.core.exceptions import OrderNotFound
import random
import string
from app.models.product import Product, ProductVariant
from app.services.order_tracking_service import OrderTrackingService
from app.schemas.order_tracking import OrderStatusUpdate, OrderTrackingResponse
from app.services.shiprocket import create_return_shipment, sync_order_tracking
from app.services.return_service import (
    RETURN_STATUS_REQUESTED,
    build_return_eligibility_payload,
    isoformat_z,
    refresh_return_status,
    mark_order_delivered,
)
from app.services.order_tracking_service import build_status_timeline, normalize_public_status
from app.core.pricing import calculate_shipping, calculate_tax, money
from app.utils.response import success
from app.core.cache import invalidate_product_cache
from app.utils.order_utils import generate_order_number
from app.core.rate_limiter import limiter
import logging


router = APIRouter()
logger = logging.getLogger(__name__)


def _order_loader_options():
    return (
        selectinload(Order.items)
        .joinedload(OrderItem.product)
        .selectinload(Product.images),
        selectinload(Order.items).joinedload(OrderItem.variant),
        joinedload(Order.payment),
        joinedload(Order.shipping_address),
    )


def _order_query(db: Session):
    return db.query(Order).options(*_order_loader_options())


def _to_decimal(value) -> Decimal:
    return money(value)


def _isoformat_or_none(value: Optional[datetime]) -> Optional[str]:
    return isoformat_z(value)


def _serialize_order_item(item: OrderItem) -> dict:
    if item is None:
        return {}

    image = None
    if item.product and getattr(item.product, "images", None):
        primary_image = next((img.image_url for img in item.product.images if img.is_primary), None)
        image = primary_image or item.product.images[0].image_url

    size = None
    if item.variant and getattr(item.variant, "size", None):
        size = item.variant.size

    return {
        "id": getattr(item, "id", None),
        "product_id": getattr(item, "product_id", None),
        "product_name": getattr(item, "product_name", None),
        "variant_details": getattr(item, "variant_details", None),
        "quantity": getattr(item, "quantity", 0),
        "unit_price": getattr(item, "unit_price", 0),
        "price": getattr(item, "unit_price", 0),
        "total_price": getattr(item, "total_price", 0),
        "image": image,
        "size": size,
    }


def _serialize_order_address(order: Order) -> dict:
    address = order.shipping_address
    if not address:
        return {}

    return {
        "id": address.id,
        "name": address.full_name,
        "full_name": address.full_name,
        "phone": address.phone,
        "address": address.address_line1,
        "address_line1": address.address_line1,
        "address_line2": address.address_line2,
        "city": address.city,
        "state": address.state,
        "pincode": address.pincode,
        "country": address.country,
    }


def _serialize_order(order: Order) -> dict:
    if order is None:
        return {}

    payment = order.payment
    payment_status = payment.payment_status.value if payment and payment.payment_status else "pending"
    payment_method = payment.payment_method.value if payment and payment.payment_method else "razorpay"
    items = []
    for item in getattr(order, "items", []) or []:
        serialized_item = _serialize_order_item(item)
        if serialized_item:
            items.append(serialized_item)

    status_value = order.status.value if getattr(order, "status", None) else "placed"
    public_status = normalize_public_status(order.status).name if getattr(order, "status", None) else "PLACED"
    tracking_number = getattr(order, "tracking_number", None) or getattr(order, "awb_code", None)
    shipping_address = _serialize_order_address(order)

    return {
        "id": order.id,
        "order_id": order.id,
        "order_number": order.order_number,
        "status": status_value,
        "public_status": public_status,
        "payment_status": payment_status,
        "payment_method": payment_method,
        "subtotal": order.subtotal,
        "tax": order.tax_amount,
        "tax_amount": order.tax_amount,
        "shipping_amount": order.shipping_charge,
        "shipping_charge": order.shipping_charge,
        "discount": order.discount_amount,
        "discount_amount": order.discount_amount,
        "total": order.total_amount,
        "grand_total": order.total_amount,
        "total_amount": order.total_amount,
        "items": items,
        "shipping_address": shipping_address,
        "address": shipping_address,
        "created_at": order.created_at,
        "estimated_delivery": order.estimated_delivery_date,
        "tracking_number": tracking_number,
        "shiprocket_order_id": order.shiprocket_order_id,
        "shipment_id": order.shipment_id,
        "tracking_id": order.tracking_id,
        "awb_code": order.awb_code,
        "tracking_url": order.tracking_url,
        "courier_name": order.courier_name or order.carrier_name,
        "carrier_name": order.carrier_name,
        "current_location": order.current_location,
        "shiprocket_last_status": order.shiprocket_last_status,
        "courier_status": order.courier_status,
        "pickup_scheduled_at": order.pickup_scheduled_at,
        "delivery_date": order.delivery_date,
        "customer_notes": order.customer_notes,
        "delivered_at": order.delivered_at,
        "return_deadline": order.return_deadline,
        "return_status": order.return_status,
        "timeline": build_status_timeline(order.status) if getattr(order, "status", None) else [],
    }


def _server_time() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _tracking_success(order_payload: dict, message: str = "Order retrieved") -> dict:
    return {
        "success": True,
        "status": "success",
        "message": message,
        "data": order_payload,
        "order": order_payload,
    }


def _build_tracking_payload(order: Order, live_tracking=None) -> dict:
    public_status = normalize_public_status(order.status).name
    timeline = build_status_timeline(order.status)

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "status": public_status,
        "payment_status": (
            order.payment.payment_status.value
            if order.payment and order.payment.payment_status
            else "pending"
        ),
        "created_at": _isoformat_or_none(order.created_at),
        "awb_code": order.awb_code or order.tracking_number,
        "courier": order.courier_name or order.carrier_name,
        "courier_name": order.courier_name or order.carrier_name,
        "location": order.current_location,
        "expected_delivery": _isoformat_or_none(order.estimated_delivery_date),
        "tracking_url": order.tracking_url,
        "shiprocket_status": order.shiprocket_last_status,
        "tracking": {
            "current_status": public_status,
            "location": order.current_location,
            "expected_delivery": _isoformat_or_none(order.estimated_delivery_date),
            "timeline": timeline,
        },
        "timeline": timeline,
        "live": live_tracking.raw_response if live_tracking else None,
    }


def _tracking_failed(message: str, data: Optional[dict] = None) -> dict:
    return {
        "success": False,
        "status": "failed",
        "message": message,
        "data": data,
        "order": None,
    }


def _resolve_order_reference(
    db: Session,
    order_reference: str,
    current_user: Optional[User] = None,
    allow_public_numeric: bool = False,
) -> Order | None:
    query = _order_query(db)
    if current_user and current_user.role.value == "admin":
        order = query.filter(Order.order_number == order_reference).first()
        if order:
            return order
    elif current_user:
        order = query.filter(
            Order.order_number == order_reference,
            Order.user_id == current_user.id,
        ).first()
        if order:
            return order
    elif allow_public_numeric:
        order = query.filter(Order.order_number == order_reference).first()
        if order:
            return order

    if not order_reference.isdigit():
        return None

    numeric_id = int(order_reference)
    query = query.filter(Order.id == numeric_id)
    if allow_public_numeric:
        return query.first()
    if current_user and current_user.role.value == "admin":
        return query.first()
    if current_user:
        return query.filter(Order.user_id == current_user.id).first()
    return None


def _can_access_order(order: Order, current_user: Optional[User]) -> bool:
    if current_user is None:
        return False
    return current_user.role.value == "admin" or order.user_id == current_user.id


def _order_unit_price(product: Product, variant: ProductVariant) -> Decimal:
    base_price = product.sale_price if product.sale_price is not None else product.base_price
    return money(base_price) + money(variant.additional_price)


def _variant_details(variant: ProductVariant) -> str:
    details = f"Size: {variant.size}"
    if variant.color:
        details += f", Color: {variant.color}"
    return details


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=dict)
@limiter.limit("10/minute")
def create_order(
    request: Request,
    order_data: OrderCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Create a COD order from the authenticated user's cart."""
    existing_order = (
        _order_query(db)
        .filter(Order.idempotency_key == order_data.idempotency_key)
        .first()
    )
    if existing_order:
        if existing_order.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key already used",
            )
        return success(data=_serialize_order(existing_order), message="Order already created")

    if order_data.payment_method != "cod":
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Use the checkout payment intent flow for Razorpay orders",
        )

    shipping_address = (
        db.query(Address)
        .filter(Address.id == order_data.shipping_address_id, Address.user_id == current_user.id)
        .first()
    )
    billing_address = (
        db.query(Address)
        .filter(Address.id == order_data.billing_address_id, Address.user_id == current_user.id)
        .first()
    )
    if not shipping_address or not billing_address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")

    cart_items = (
        db.query(CartItem)
        .options(joinedload(CartItem.product), joinedload(CartItem.variant))
        .filter(CartItem.user_id == current_user.id)
        .order_by(CartItem.id.asc())
        .all()
    )
    if not cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")

    try:
        subtotal = money(0)
        order_items: list[OrderItem] = []
        touched_product_slugs: set[str] = set()

        for cart_item in cart_items:
            product = cart_item.product
            variant = (
                db.query(ProductVariant)
                .filter(ProductVariant.id == cart_item.variant_id, ProductVariant.product_id == cart_item.product_id)
                .with_for_update()
                .first()
            )
            if not product or not variant or not product.is_active or not variant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cart contains unavailable items",
                )
            if variant.stock_quantity < cart_item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for {product.name}",
                )

            unit_price = _order_unit_price(product, variant)
            line_total = money(unit_price * cart_item.quantity)
            subtotal += line_total
            variant.stock_quantity -= cart_item.quantity
            touched_product_slugs.add(product.slug)
            order_items.append(
                OrderItem(
                    product_id=product.id,
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_details=_variant_details(variant),
                    quantity=cart_item.quantity,
                    unit_price=unit_price,
                    total_price=line_total,
                )
            )

        shipping = calculate_shipping(subtotal)
        tax = calculate_tax(subtotal + shipping)
        total = money(subtotal + shipping + tax)
        order = Order(
            order_number=generate_order_number(db),
            user_id=current_user.id,
            subtotal=subtotal,
            tax_amount=tax,
            shipping_charge=shipping,
            discount_amount=money(0),
            total_amount=total,
            status=OrderStatus.PLACED,
            stock_deducted=True,
            idempotency_key=order_data.idempotency_key,
            shipping_address_id=shipping_address.id,
            billing_address_id=billing_address.id,
            customer_notes=order_data.customer_notes,
        )
        db.add(order)
        db.flush()

        for order_item in order_items:
            order_item.order_id = order.id
            db.add(order_item)

        db.add(
            Payment(
                order_id=order.id,
                payment_method=PaymentMethod.COD,
                payment_status=PaymentStatus.PENDING,
                amount=total,
                currency="INR",
            )
        )
        db.query(CartItem).filter(CartItem.user_id == current_user.id).delete(synchronize_session=False)
        db.commit()
        invalidate_product_cache(touched_product_slugs)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("order_create_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create order",
        )

    created_order = _order_query(db).filter(Order.id == order.id).first()
    return success(data=_serialize_order(created_order), message="Order created successfully")


@router.get("/", response_model=dict)
@limiter.limit("30/minute")
def get_user_orders(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's order history"""
    try:
        logger.info("orders_fetch_started user_id=%s page=%s limit=%s", current_user.id, page, limit)

        base_query = _order_query(db).filter(Order.user_id == current_user.id)
        total = base_query.count()

        orders = (
            base_query
            .order_by(Order.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        orders_response = []
        for order in orders:
            try:
                refresh_return_status(order)
                payload = _serialize_order(order)
                if payload:
                    orders_response.append(payload)
            except Exception:
                logger.exception(
                    "orders_serialize_failed user_id=%s order_id=%s",
                    current_user.id,
                    getattr(order, "id", None),
                )

        db.commit()
        return success(
            data=orders_response,
            message="Orders retrieved",
            meta={
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit if limit else 0,
                "server_time": _server_time(),
            },
        )
    except Exception as exc:
        db.rollback()
        logger.error("Orders error for user_id=%s", current_user.id, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "Failed to fetch orders",
                "data": [],
                "errors": None,
                "meta": {
                    "page": page,
                    "limit": limit,
                    "server_time": _server_time(),
                },
            },
        )


@router.get("/user/{user_id}", response_model=dict)
@limiter.limit("30/minute")
def get_user_orders_by_id(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get orders for a specific user id."""
    if current_user.id != user_id and current_user.role.value != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")

    orders = (
        _order_query(db)
        .filter(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return success(data=[_serialize_order(order) for order in orders], message="Orders retrieved")


@router.get("/{order_reference}", response_model=dict)
@limiter.limit("30/minute")
def get_order_detail(
    request: Request,
    order_reference: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get order details for the authenticated order owner."""
    try:
        order = _resolve_order_reference(
            db=db,
            order_reference=order_reference,
            current_user=current_user,
        )
        if not order:
            return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_tracking_failed("Order not found"))
        if not _can_access_order(order, current_user):
            return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_tracking_failed("Order not found"))

        refresh_return_status(order)
        db.commit()
        payload = _serialize_order(order)
        payload["expires_at"] = _isoformat_or_none(order.expires_at)
        return _tracking_success(payload, message="Order detail retrieved")
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_tracking_failed(str(exc.detail)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=_tracking_failed("Failed to retrieve order"),
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
    order = _order_query(db).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise OrderNotFound()
    
    # Can only cancel if not shipped
    if order.status in [OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel shipped/delivered orders"
        )
    
    previous_status = order.status
    order.status = OrderStatus.CANCELLED

    # Restore stock only if this order already deducted inventory.
    if order.stock_deducted and previous_status in {OrderStatus.PLACED, OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}:
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


@router.post("/{order_id}/deliver", response_model=dict)
@limiter.limit("20/minute")
def mark_delivered(
    request: Request,
    order_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    order = _order_query(db).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    mark_order_delivered(order)
    db.commit()
    db.refresh(order)
    return success(
        data={
            "order_id": order.id,
            "status": order.status.value,
            "delivered_at": _isoformat_or_none(order.delivered_at),
            "return_deadline": _isoformat_or_none(order.return_deadline),
            "return_status": order.return_status,
        },
        message="Order marked as delivered",
    )


@router.post("/{order_id}/return", response_model=dict)
@limiter.limit("10/minute")
def request_order_return(
    request: Request,
    order_id: int,
    payload: dict = Body(default={}),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    order = (
        _order_query(db)
        .filter(Order.id == order_id, Order.user_id == current_user.id)
        .with_for_update()
        .first()
    )
    if not order:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_tracking_failed("Order not found"))

    eligibility = build_return_eligibility_payload(order)
    if not eligibility["eligible"]:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_tracking_failed("Return window expired", eligibility),
        )

    existing_requests = {item.order_item_id for item in order.returns}
    reason_value = payload.get("reason", ReturnReason.OTHER.value)
    try:
        reason = ReturnReason(reason_value)
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_tracking_failed("Invalid return reason"),
        )
    description = payload.get("description")
    created_requests = []
    created_return_requests: list[ReturnRequest] = []
    for item in order.items:
        if item.id in existing_requests:
            continue
        return_request = ReturnRequest(
            order_id=order.id,
            order_item_id=item.id,
            user_id=current_user.id,
            reason=reason,
            description=description,
            refund_amount=item.total_price,
            refund_method="original_payment",
        )
        db.add(return_request)
        created_requests.append(item.id)
        created_return_requests.append(return_request)

    if not created_requests:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_tracking_failed("Return request already exists"),
        )

    order.status = OrderStatus.RETURN_REQUESTED
    order.return_status = RETURN_STATUS_REQUESTED
    db.commit()
    db.refresh(order)
    return_shipment = None
    if created_return_requests:
        try:
            return_request = created_return_requests[0]
            db.refresh(return_request)
            shipment = create_return_shipment(order, return_request)
            return_request.shiprocket_return_order_id = shipment.shiprocket_order_id
            return_request.shiprocket_return_shipment_id = shipment.shipment_id
            return_request.return_awb_code = shipment.awb_code
            return_request.return_tracking_url = shipment.tracking_url
            return_request.return_courier_name = shipment.courier_name
            db.commit()
            return_shipment = {
                "shipment_id": shipment.shipment_id,
                "awb_code": shipment.awb_code,
                "courier_name": shipment.courier_name,
                "tracking_url": shipment.tracking_url,
            }
        except Exception:
            db.rollback()
    response_payload = {
        "order_id": order.id,
        "status": normalize_public_status(order.status).name,
        "requested_items": created_requests,
        "return_deadline": _isoformat_or_none(order.return_deadline),
        "return_shipment": return_shipment,
    }
    return {
        "success": True,
        "status": "success",
        "message": "Return request submitted",
        "data": response_payload,
    }


@router.get("/{order_id}/return-eligibility", response_model=dict)
@limiter.limit("60/minute")
def get_return_eligibility(
    request: Request,
    order_id: int,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    order = (
        _order_query(db)
        .filter(Order.id == order_id, Order.user_id == current_user.id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    payload = build_return_eligibility_payload(order)
    db.commit()
    response.headers["Cache-Control"] = "private, max-age=30"
    return success(data=payload, message="Return eligibility retrieved")


@router.get("/{order_reference}/tracking", response_model=dict)
@limiter.limit("30/minute")
def get_order_tracking(
    request: Request,
    order_reference: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get order tracking information."""
    order = _resolve_order_reference(
        db=db,
        order_reference=order_reference,
        current_user=current_user,
        allow_public_numeric=True,
    )
    if not order:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_tracking_failed("Order not found"))
    if current_user is not None and not _can_access_order(order, current_user):
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_tracking_failed("Order not found"))

    live_tracking = None
    if order.awb_code:
        try:
            live_tracking = sync_order_tracking(order)
            if live_tracking is not None:
                db.commit()
                db.refresh(order)
        except Exception:
            db.rollback()

    payload = _build_tracking_payload(order, live_tracking=live_tracking)
    return {
        "success": True,
        "status": "success",
        "message": "Order tracking retrieved",
        "data": payload,
        "order": _serialize_order(order),
    }


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
        _order_query(db)
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

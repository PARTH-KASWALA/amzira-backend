import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_active_user, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.address import Address
from app.models.cart import CartItem
from app.models.checkout_payment_intent import (
    CheckoutPaymentIntent,
    CheckoutPaymentIntentStatus,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import Payment, PaymentMethod, PaymentStatus
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.schemas.commerce_checkout import (
    CheckoutRequest,
    CreatePaymentOrderRequest,
    VerifyPaymentRequest,
)
from app.services.checkout_payment_service import (
    build_checkout_payment_response,
    create_order_from_checkout_intent,
    get_payment_mapped_order,
    sync_intent_success,
)
from app.services.payment_service import get_razorpay_client, verify_payment_signature
from app.services.shiprocket import check_pincode_serviceability, validate_shiprocket_configuration
from app.tasks.order_tasks import dispatch_fulfill_order
from app.core.pricing import calculate_shipping, calculate_tax, money, money_float
from app.utils.response import success


router = APIRouter()
logger = logging.getLogger(__name__)


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _ensure_user_owns_resource(current_user: User, user_id: int) -> None:
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this resource",
        )


def _unit_price(product: Product, variant: ProductVariant) -> Decimal:
    base_price = product.sale_price if product.sale_price is not None else product.base_price
    return money(base_price) + money(variant.additional_price)


def _get_address_or_404(db: Session, user_id: int, address_id: int) -> Address:
    address = (
        db.query(Address)
        .filter(Address.id == address_id, Address.user_id == user_id)
        .first()
    )
    if not address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    return address


def _validate_serviceability_if_configured(address: Address, *, cod: bool = False) -> None:
    if not address.pincode:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Delivery pincode is required")
    if not validate_shiprocket_configuration(strict=False):
        return
    try:
        check_pincode_serviceability(address.pincode, cod=cod)
    except Exception as exc:
        detail = str(exc) or "Delivery pincode is not serviceable"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


def _get_cart_items_or_400(db: Session, user_id: int) -> list[CartItem]:
    cart_items = (
        db.query(CartItem)
        .filter(CartItem.user_id == user_id)
        .order_by(CartItem.id.asc())
        .all()
    )
    if not cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")
    return cart_items


def _build_checkout_snapshot(cart_items: list[CartItem]) -> dict:
    items = []
    subtotal = money(0)

    for cart_item in cart_items:
        product = cart_item.product
        variant = cart_item.variant
        if not product or not variant:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart contains unavailable items")

        primary_image = next((img.image_url for img in product.images if img.is_primary), None)
        if not primary_image and product.images:
            primary_image = product.images[0].image_url

        unit_price = _unit_price(product, variant)
        total_price = money(unit_price * cart_item.quantity)
        subtotal += total_price

        variant_details = f"Size: {variant.size}"
        if variant.color:
            variant_details += f", Color: {variant.color}"

        items.append(
            {
                "cart_item_id": cart_item.id,
                "product_id": product.id,
                "product_name": product.name,
                "product_image": primary_image,
                "variant_id": variant.id,
                "variant_details": variant_details,
                "quantity": cart_item.quantity,
                "unit_price": money_float(unit_price),
                "total_price": money_float(total_price),
            }
        )

    shipping = calculate_shipping(subtotal)
    tax = calculate_tax(subtotal + shipping)
    total = money(subtotal + shipping + tax)
    return {
        "items": items,
        "subtotal": money_float(subtotal),
        "shipping": money_float(shipping),
        "shipping_amount": money_float(shipping),
        "tax": money_float(tax),
        "total": money_float(total),
    }


def _lock_variants_for_snapshot(db: Session, items: list[dict]) -> dict[int, ProductVariant]:
    requested_quantities: dict[int, int] = {}
    for item in items:
        variant_id = int(item["variant_id"])
        requested_quantities[variant_id] = requested_quantities.get(variant_id, 0) + int(item["quantity"])

    locked_variants: dict[int, ProductVariant] = {}
    for variant_id in sorted(requested_quantities.keys()):
        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == variant_id)
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found")
        if variant.stock_quantity < requested_quantities[variant_id]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for variant {variant.id}",
            )
        locked_variants[variant_id] = variant

    return locked_variants


@router.post("/checkout")
def validate_checkout(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _ensure_user_owns_resource(current_user, payload.user_id)
    _get_user_or_404(db, payload.user_id)
    address = _get_address_or_404(db, payload.user_id, payload.address_id)
    _validate_serviceability_if_configured(address, cod=False)

    cart_items = _get_cart_items_or_400(db, payload.user_id)
    cart_summary = _build_checkout_snapshot(cart_items)
    _lock_variants_for_snapshot(db, cart_summary["items"])

    return success(
        data={
            **cart_summary,
            "address_id": address.id,
            "status": "validated",
        },
        message="Checkout validated",
    )


@router.post("/create-payment-order")
def create_payment_order(
    payload: CreatePaymentOrderRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _ensure_user_owns_resource(current_user, payload.user_id)
    _get_user_or_404(db, payload.user_id)
    user = _get_user_or_404(db, payload.user_id)
    address = _get_address_or_404(db, payload.user_id, payload.address_id)
    _validate_serviceability_if_configured(address, cod=False)
    cart_items = _get_cart_items_or_400(db, payload.user_id)
    snapshot = _build_checkout_snapshot(cart_items)
    _lock_variants_for_snapshot(db, snapshot["items"])

    amount_paise = int(round(snapshot["total"] * 100))
    try:
        razorpay_client = get_razorpay_client()
        logger.info(
            "creating_razorpay_order user_id=%s address_id=%s amount_paise=%s",
            payload.user_id,
            payload.address_id,
            amount_paise,
        )
        razorpay_order = razorpay_client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "receipt": f"checkout-{payload.user_id}-{uuid4().hex[:8]}",
                "notes": {
                    "user_id": str(payload.user_id),
                    "address_id": str(payload.address_id),
                    "email": user.email,
                },
            }
        )

        intent = CheckoutPaymentIntent(
            user_id=payload.user_id,
            address_id=address.id,
            razorpay_order_id=razorpay_order["id"],
            amount=round(snapshot["total"], 2),
            currency="INR",
            subtotal=snapshot["subtotal"],
            tax_amount=snapshot["tax"],
            total_amount=snapshot["total"],
            status=CheckoutPaymentIntentStatus.PENDING,
            cart_snapshot=json.dumps(snapshot["items"]),
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        db.add(intent)
        db.commit()
        db.refresh(intent)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "persist_payment_session_failed user_id=%s address_id=%s amount_paise=%s",
            payload.user_id,
            payload.address_id,
            amount_paise,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to persist payment session",
        )
    except Exception as exc:
        db.rollback()
        logger.exception(
            "create_razorpay_order_failed user_id=%s address_id=%s amount_paise=%s error=%s",
            payload.user_id,
            payload.address_id,
            amount_paise,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Unable to create Razorpay order: {str(exc)}"
                if settings.ENVIRONMENT != "production"
                else "Unable to create Razorpay order"
            ),
        )

    return success(
        data={
            "razorpay_order_id": razorpay_order["id"],
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount": amount_paise,
            "currency": "INR",
            "subtotal": snapshot["subtotal"],
            "tax": snapshot["tax"],
            "total": snapshot["total"],
        },
        message="Payment order created",
    )


@router.post("/verify-payment", status_code=status.HTTP_201_CREATED)
def verify_payment(
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    intent = (
        db.query(CheckoutPaymentIntent)
        .filter(CheckoutPaymentIntent.razorpay_order_id == payload.razorpay_order_id)
        .with_for_update()
        .first()
    )
    if not intent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")

    if intent.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment does not belong to this user")

    if payload.user_id is not None and intent.user_id != payload.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment does not belong to this user")

    if payload.address_id is not None and intent.address_id != payload.address_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment does not belong to this user")

    if intent.expires_at and intent.expires_at < datetime.utcnow():
        intent.status = CheckoutPaymentIntentStatus.EXPIRED
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment session expired")

    existing_order = get_payment_mapped_order(
        db,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_order_id=payload.razorpay_order_id,
    )
    if existing_order is not None:
        sync_intent_success(
            intent,
            order_id=existing_order.id,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_signature=payload.razorpay_signature,
        )
        db.commit()
        response = success(
            data=build_checkout_payment_response(existing_order, "Payment already verified"),
            message="Payment already verified",
        )
        response["status"] = "success"
        response["order_id"] = existing_order.id
        return response

    if intent.status == CheckoutPaymentIntentStatus.SUCCESS and intent.created_order_id:
        order = db.query(Order).filter(Order.id == intent.created_order_id).first()
        if order:
            response = success(
                data=build_checkout_payment_response(order, "Payment already verified"),
                message="Payment already verified",
            )
            response["status"] = "success"
            response["order_id"] = order.id
            return response

    if not verify_payment_signature(
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
    ):
        intent.status = CheckoutPaymentIntentStatus.FAILED
        intent.razorpay_payment_id = payload.razorpay_payment_id
        intent.razorpay_signature = payload.razorpay_signature
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment signature")

    try:
        order = create_order_from_checkout_intent(
            db,
            intent=intent,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_signature=payload.razorpay_signature,
        )
        db.commit()
        db.refresh(order)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    dispatch_fulfill_order(order.id)

    response = success(
        data=build_checkout_payment_response(order, "Payment verified and order created"),
        message="Payment verified and order created",
    )
    response["status"] = "success"
    response["order_id"] = order.id
    return response

@router.get("/admin/orders")
def admin_list_orders(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    status_filter: str | None = Query(None),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = request
    query = (
        db.query(Order)
        .options(joinedload(Order.user))
        .order_by(Order.created_at.desc())
    )
    if status_filter:
        query = query.filter(Order.status == status_filter)
    total = query.count()
    orders = query.offset((page - 1) * limit).limit(limit).all()
    return success(
        data={
            "orders": [
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "user_id": order.user_id,
                    "customer_name": order.user.full_name if order.user else None,
                    "total_amount": float(order.total_amount),
                    "status": order.status.value if isinstance(order.status, OrderStatus) else str(order.status),
                    "created_at": order.created_at,
                }
                for order in orders
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit,
        },
        message="Orders retrieved",
    )

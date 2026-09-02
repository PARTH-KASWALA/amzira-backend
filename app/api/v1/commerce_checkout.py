import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
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
    build_cart_fingerprint,
    build_checkout_payment_response,
    create_order_from_checkout_intent,
    expire_checkout_intents,
    get_payment_mapped_order,
    quote_checkout_coupon,
    release_checkout_stock,
    reserve_checkout_coupon,
    reserve_checkout_stock,
    sync_intent_success,
)
from app.services.payment_service import get_razorpay_client, verify_payment_signature
from app.services.shiprocket import check_pincode_serviceability, validate_shiprocket_configuration
from app.tasks.order_tasks import dispatch_fulfill_order
from app.core.pricing import calculate_shipping, calculate_tax, money, money_float
from app.utils.response import success
from app.core.rate_limiter import limiter


router = APIRouter()
logger = logging.getLogger(__name__)


def _ensure_checkout_enabled() -> None:
    if not settings.CHECKOUT_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Checkout is temporarily unavailable",
        )


@router.get("/commerce/status")
def commerce_status():
    return success(
        data={
            "checkout_enabled": settings.CHECKOUT_ENABLED,
            "cod_enabled": settings.CHECKOUT_ENABLED and settings.COD_ENABLED,
        },
        message="Commerce status retrieved",
    )


def _dispatch_fulfillment_safely(order_id: int) -> None:
    try:
        dispatch_fulfill_order(order_id)
    except Exception:
        logger.exception("order_fulfillment_dispatch_failed order_id=%s", order_id)


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
@limiter.limit("5/10 minutes")
def create_payment_order(
    request: Request,
    payload: CreatePaymentOrderRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _ensure_checkout_enabled()
    _ensure_user_owns_resource(current_user, payload.user_id)
    user = (
        db.query(User)
        .filter(User.id == payload.user_id, User.is_active == True)
        .with_for_update()
        .first()
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    address = _get_address_or_404(db, payload.user_id, payload.address_id)
    _validate_serviceability_if_configured(address, cod=False)
    cart_items = _get_cart_items_or_400(db, payload.user_id)
    snapshot = _build_checkout_snapshot(cart_items)
    fingerprint = build_cart_fingerprint(
        user_id=payload.user_id,
        address_id=address.id,
        items=snapshot["items"],
        coupon_code=payload.coupon_code,
    )
    expire_checkout_intents(db, user_id=payload.user_id)
    active_intent = (
        db.query(CheckoutPaymentIntent)
        .filter(
            CheckoutPaymentIntent.user_id == payload.user_id,
            CheckoutPaymentIntent.status == CheckoutPaymentIntentStatus.PENDING,
            CheckoutPaymentIntent.expires_at > datetime.utcnow(),
        )
        .with_for_update()
        .first()
    )
    if active_intent:
        if active_intent.cart_fingerprint != fingerprint:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another checkout is already in progress for this account",
            )
        return success(
            data={
                "razorpay_order_id": active_intent.razorpay_order_id,
                "razorpay_key_id": settings.RAZORPAY_KEY_ID,
                "payment_required": True,
                "amount": int(money(active_intent.total_amount) * 100),
                "currency": active_intent.currency,
                "subtotal": float(active_intent.subtotal),
                "discount": float(active_intent.discount_amount),
                "coupon_code": active_intent.coupon_code,
                "tax": float(active_intent.tax_amount),
                "total": float(active_intent.total_amount),
            },
            message="Existing payment order retrieved",
        )

    coupon = None
    discount = money(0)
    if payload.coupon_code:
        coupon, discount = quote_checkout_coupon(
            db,
            user_id=payload.user_id,
            coupon_code=payload.coupon_code,
            subtotal=money(snapshot["subtotal"]),
        )
        discounted_subtotal = max(money(0), money(snapshot["subtotal"]) - discount)
        tax = calculate_tax(discounted_subtotal + money(snapshot["shipping"]))
        total = money(discounted_subtotal + money(snapshot["shipping"]) + tax)
        snapshot["discount"] = money_float(discount)
        snapshot["tax"] = money_float(tax)
        snapshot["total"] = money_float(total)
    else:
        snapshot["discount"] = 0.0

    amount_paise = int(money(snapshot["total"]) * 100)
    if amount_paise == 0:
        internal_order_id = f"promo_order_{uuid4().hex}"
        internal_payment_id = f"promo_payment_{uuid4().hex}"
        try:
            intent = CheckoutPaymentIntent(
                user_id=payload.user_id,
                address_id=address.id,
                razorpay_order_id=internal_order_id,
                amount=money(0),
                currency="INR",
                subtotal=snapshot["subtotal"],
                shipping_amount=snapshot["shipping"],
                tax_amount=snapshot["tax"],
                discount_amount=snapshot["discount"],
                total_amount=snapshot["total"],
                status=CheckoutPaymentIntentStatus.PENDING,
                cart_snapshot=json.dumps(snapshot["items"]),
                cart_fingerprint=fingerprint,
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            )
            db.add(intent)
            db.flush()
            reserve_checkout_coupon(intent, coupon)
            reserve_checkout_stock(db, intent)
            order = create_order_from_checkout_intent(
                db,
                intent=intent,
                razorpay_payment_id=internal_payment_id,
                razorpay_signature=None,
                payment_method=PaymentMethod.PROMOTIONAL,
            )
            db.commit()
            db.refresh(order)
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("fully_discounted_checkout_failed user_id=%s address_id=%s", payload.user_id, address.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to complete the fully discounted order",
            )

        _dispatch_fulfillment_safely(order.id)
        return success(
            data={
                "payment_required": False,
                "order_id": order.id,
                "order_number": order.order_number,
                "amount": 0,
                "currency": "INR",
                "subtotal": snapshot["subtotal"],
                "shipping": snapshot["shipping"],
                "discount": snapshot["discount"],
                "coupon_code": payload.coupon_code,
                "tax": snapshot["tax"],
                "total": snapshot["total"],
            },
            message="Fully discounted order created",
        )

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
            shipping_amount=snapshot["shipping"],
            tax_amount=snapshot["tax"],
            discount_amount=snapshot["discount"],
            total_amount=snapshot["total"],
            status=CheckoutPaymentIntentStatus.PENDING,
            cart_snapshot=json.dumps(snapshot["items"]),
            cart_fingerprint=fingerprint,
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        db.add(intent)
        db.flush()
        reserve_checkout_coupon(intent, coupon)
        reserve_checkout_stock(db, intent)
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
            "payment_required": True,
            "amount": amount_paise,
            "currency": "INR",
            "subtotal": snapshot["subtotal"],
            "discount": snapshot["discount"],
            "coupon_code": payload.coupon_code,
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
        release_checkout_stock(db, intent, reason="Checkout reservation expired before verification")
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

    _dispatch_fulfillment_safely(order.id)

    response = success(
        data=build_checkout_payment_response(order, "Payment verified and order created"),
        message="Payment verified and order created",
    )
    response["status"] = "success"
    response["order_id"] = order.id
    return response

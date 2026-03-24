import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

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
    AddressSummaryResponse,
    CartAddRequest,
    CartUpdateRequest,
    CheckoutAddressCreate,
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
from app.services.shiprocket import fulfill_order
from app.services.payment_service import get_razorpay_client, verify_payment_signature
from app.core.pricing import calculate_tax, money, money_float
from app.utils.response import success
from app.api.v1.orders import generate_order_number


router = APIRouter()
logger = logging.getLogger(__name__)


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _get_default_variant(product: Product) -> ProductVariant:
    variants = [
        variant for variant in product.variants
        if variant.is_active and (variant.stock_quantity or 0) > 0
    ]
    if not variants:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product is out of stock")
    return sorted(variants, key=lambda variant: variant.id)[0]


def _get_variant_or_404(db: Session, product_id: int, variant_id: int) -> ProductVariant:
    variant = (
        db.query(ProductVariant)
        .filter(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
            ProductVariant.is_active == True,
        )
        .first()
    )
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found")
    return variant


def _unit_price(product: Product, variant: ProductVariant) -> Decimal:
    base_price = product.sale_price if product.sale_price is not None else product.base_price
    return money(base_price) + money(variant.additional_price)


def _build_cart_summary(db: Session, user_id: int) -> dict:
    cart_items = (
        db.query(CartItem)
        .filter(CartItem.user_id == user_id)
        .order_by(CartItem.created_at.asc(), CartItem.id.asc())
        .all()
    )

    items = []
    subtotal = money(0)
    for cart_item in cart_items:
        product = cart_item.product
        variant = cart_item.variant
        if not product or not variant:
            continue

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

    tax = calculate_tax(subtotal)
    total = money(subtotal + tax)
    return {
        "user_id": user_id,
        "items": items,
        "subtotal": money_float(subtotal),
        "tax": money_float(tax),
        "total": money_float(total),
    }


def _serialize_address(address: Address) -> dict:
    return {
        "id": address.id,
        "user_id": address.user_id,
        "name": address.full_name,
        "phone": address.phone,
        "address_line": address.address_line1,
        "city": address.city,
        "state": address.state,
        "pincode": address.pincode,
        "is_default": bool(address.is_default),
    }


def _get_address_or_404(db: Session, user_id: int, address_id: int) -> Address:
    address = (
        db.query(Address)
        .filter(Address.id == address_id, Address.user_id == user_id)
        .first()
    )
    if not address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    return address


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

    tax = calculate_tax(subtotal)
    total = money(subtotal + tax)
    return {
        "items": items,
        "subtotal": money_float(subtotal),
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


@router.post("/cart/add")
def add_to_cart(
    payload: CartAddRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    _ = request
    _get_user_or_404(db, payload.user_id)

    product = (
        db.query(Product)
        .filter(Product.id == payload.product_id, Product.is_active == True)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    variant = (
        _get_variant_or_404(db, product.id, payload.variant_id)
        if payload.variant_id
        else _get_default_variant(product)
    )
    if variant.stock_quantity < payload.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {variant.stock_quantity} items left in stock",
        )

    cart_item = (
        db.query(CartItem)
        .filter(
            CartItem.user_id == payload.user_id,
            CartItem.product_id == payload.product_id,
            CartItem.variant_id == variant.id,
        )
        .first()
    )

    if cart_item:
        next_quantity = cart_item.quantity + payload.quantity
        if variant.stock_quantity < next_quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only {variant.stock_quantity} items left in stock",
            )
        cart_item.quantity = next_quantity
    else:
        cart_item = CartItem(
            user_id=payload.user_id,
            product_id=payload.product_id,
            variant_id=variant.id,
            quantity=payload.quantity,
            price_at_addition=_unit_price(product, variant),
        )
        db.add(cart_item)

    db.commit()
    db.refresh(cart_item)
    return success(
        data={
            "cart_item_id": cart_item.id,
            "product_id": product.id,
            "variant_id": variant.id,
            "quantity": cart_item.quantity,
        },
        message="Item added to cart",
    )


@router.get("/cart/{user_id}")
def get_cart(user_id: int, db: Session = Depends(get_db)):
    _get_user_or_404(db, user_id)
    return success(data=_build_cart_summary(db, user_id), message="Cart retrieved")


@router.put("/cart/items/{item_id}")
def update_cart_item(item_id: int, payload: CartUpdateRequest, db: Session = Depends(get_db)):
    _get_user_or_404(db, payload.user_id)

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.user_id == payload.user_id)
        .first()
    )
    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")

    variant = cart_item.variant
    if not variant or variant.stock_quantity < payload.quantity:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")

    cart_item.quantity = payload.quantity
    db.commit()
    db.refresh(cart_item)
    return success(data={"cart_item_id": cart_item.id, "quantity": cart_item.quantity}, message="Cart updated")


@router.delete("/cart/items/{item_id}")
def delete_cart_item(item_id: int, user_id: int, db: Session = Depends(get_db)):
    _get_user_or_404(db, user_id)

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.user_id == user_id)
        .first()
    )
    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")

    db.delete(cart_item)
    db.commit()
    return success(message="Cart item removed")


@router.post("/addresses", status_code=status.HTTP_201_CREATED)
def add_address(payload: CheckoutAddressCreate, db: Session = Depends(get_db)):
    _get_user_or_404(db, payload.user_id)

    existing_addresses = (
        db.query(Address)
        .filter(Address.user_id == payload.user_id)
        .order_by(Address.id.asc())
        .all()
    )

    should_be_default = payload.is_default or len(existing_addresses) == 0
    if should_be_default:
        (
            db.query(Address)
            .filter(Address.user_id == payload.user_id, Address.is_default == True)
            .update({"is_default": False})
        )

    address = Address(
        user_id=payload.user_id,
        full_name=payload.name,
        phone=payload.phone,
        address_line1=payload.address_line,
        city=payload.city,
        state=payload.state,
        pincode=payload.pincode,
        country="India",
        is_default=should_be_default,
        address_type="home",
    )
    db.add(address)
    db.commit()
    db.refresh(address)

    return success(data=_serialize_address(address), message="Address created")


@router.get("/addresses/{user_id}")
def get_addresses(user_id: int, db: Session = Depends(get_db)):
    _get_user_or_404(db, user_id)
    addresses = (
        db.query(Address)
        .filter(Address.user_id == user_id)
        .order_by(Address.is_default.desc(), Address.id.desc())
        .all()
    )
    return success(
        data=[_serialize_address(address) for address in addresses],
        message="Addresses retrieved",
    )


@router.post("/checkout")
def validate_checkout(payload: CheckoutRequest, db: Session = Depends(get_db)):
    _get_user_or_404(db, payload.user_id)
    address = _get_address_or_404(db, payload.user_id, payload.address_id)

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
def create_payment_order(payload: CreatePaymentOrderRequest, db: Session = Depends(get_db)):
    _get_user_or_404(db, payload.user_id)
    user = _get_user_or_404(db, payload.user_id)
    address = _get_address_or_404(db, payload.user_id, payload.address_id)
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

    shiprocket_result = None
    try:
        shiprocket_result = fulfill_order(order)
        if (
            shiprocket_result.get("fulfilled")
            or shiprocket_result.get("shipment_id")
            or shiprocket_result.get("shiprocket_order_id")
            or shiprocket_result.get("awb_code")
        ):
            db.commit()
            db.refresh(order)
    except Exception:
        db.rollback()
        logger.exception("checkout_shiprocket_fulfillment_failed order_id=%s", order.id)

    response = success(
        data=build_checkout_payment_response(order, "Payment verified and order created"),
        message="Payment verified and order created",
    )
    response["status"] = "success"
    response["order_id"] = order.id
    response["data"]["shiprocket"] = shiprocket_result or {"fulfilled": False}
    return response


@router.post("/orders")
def create_order(_: CheckoutRequest, db: Session = Depends(get_db)):
    _ = db
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Direct order creation is disabled. Complete payment verification first.",
    )


@router.get("/admin/orders")
def admin_list_orders(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = request
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return success(
        data=[
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
        message="Orders retrieved",
    )

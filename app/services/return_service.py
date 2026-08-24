from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus
from app.models.product import ProductVariant
from app.models.return_request import ReturnRequest, ReturnStatus

RETURN_WINDOW_HOURS = 36
RETURN_STATUS_NOT_APPLICABLE = "not_applicable"
RETURN_STATUS_ELIGIBLE = "eligible"
RETURN_STATUS_EXPIRED = "expired"
RETURN_STATUS_REQUESTED = "requested"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat_z(value: datetime | None) -> str | None:
    normalized = ensure_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def mark_order_delivered(order: Order, delivered_at: datetime | None = None) -> Order:
    delivered_time = ensure_utc(delivered_at) or utc_now()
    order.status = OrderStatus.DELIVERED
    order.delivered_at = delivered_time
    order.return_deadline = delivered_time + timedelta(hours=RETURN_WINDOW_HOURS)
    order.return_status = RETURN_STATUS_ELIGIBLE
    order.expires_at = None
    return order


def refresh_return_status(order: Order, now: datetime | None = None) -> Order:
    current_time = ensure_utc(now) or utc_now()
    deadline = ensure_utc(order.return_deadline)

    if order.status == OrderStatus.RETURN_REQUESTED:
        order.return_status = RETURN_STATUS_REQUESTED
        return order

    if order.status == OrderStatus.RETURNED:
        order.return_status = RETURN_STATUS_EXPIRED
        return order

    if order.status != OrderStatus.DELIVERED or deadline is None:
        if order.return_status is None:
            order.return_status = RETURN_STATUS_NOT_APPLICABLE
        return order

    if order.return_status == RETURN_STATUS_REQUESTED:
        return order

    order.return_status = (
        RETURN_STATUS_ELIGIBLE if current_time <= deadline else RETURN_STATUS_EXPIRED
    )
    return order


def build_return_eligibility_payload(order: Order, now: datetime | None = None) -> dict:
    current_time = ensure_utc(now) or utc_now()
    refresh_return_status(order, current_time)

    deadline = ensure_utc(order.return_deadline)
    eligible = (
        order.status == OrderStatus.DELIVERED
        and deadline is not None
        and current_time <= deadline
        and order.return_status == RETURN_STATUS_ELIGIBLE
    )
    ms_remaining = 0
    if deadline is not None:
        ms_remaining = max(0, int((deadline - current_time).total_seconds() * 1000))

    return {
        "eligible": eligible,
        "return_deadline": isoformat_z(deadline),
        "server_time": isoformat_z(current_time),
        "ms_remaining": ms_remaining,
        "return_status": order.return_status,
    }


def validate_return_request(order: Order, order_item_id: int, now: datetime | None = None) -> None:
    current_time = ensure_utc(now) or utc_now()
    refresh_return_status(order, current_time)

    if order.status not in {OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only return delivered orders",
        )

    deadline = ensure_utc(order.return_deadline)
    if deadline is None or current_time > deadline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Return window expired",
        )

    existing_request = next(
        (item for item in order.returns if item.order_item_id == order_item_id),
        None,
    )
    if existing_request is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Return request already exists for this item",
        )


def get_locked_order_for_return(db: Session, order_id: int, user_id: int) -> Order:
    order = (
        db.query(Order)
        .filter(Order.id == order_id, Order.user_id == user_id)
        .with_for_update()
        .first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


def get_existing_return_for_item(db: Session, order_item_id: int) -> ReturnRequest | None:
    return (
        db.query(ReturnRequest)
        .filter(ReturnRequest.order_item_id == order_item_id)
        .with_for_update()
        .first()
    )


def restock_return_inventory(db: Session, return_request: ReturnRequest) -> None:
    if return_request.inventory_restocked:
        return
    order_item = return_request.order_item
    variant = (
        db.query(ProductVariant)
        .filter(ProductVariant.id == order_item.variant_id)
        .with_for_update()
        .first()
    )
    if variant is not None:
        variant.stock_quantity += order_item.quantity
    return_request.inventory_restocked = True


def finalize_processed_refund(db: Session, return_request: ReturnRequest) -> None:
    if return_request.status == ReturnStatus.REFUNDED:
        return

    restock_return_inventory(db, return_request)
    return_request.status = ReturnStatus.REFUNDED
    return_request.refund_error = None
    return_request.resolved_at = utc_now()

    order = return_request.order
    payment = order.payment if order else None
    if payment is None:
        return

    db.flush()
    processed_total = sum(
        (item.refund_amount or 0)
        for item in order.returns
        if item.status == ReturnStatus.REFUNDED
    )
    payment.refunded_amount = processed_total
    all_items_refunded = bool(order.items) and all(
        any(
            request.order_item_id == order_item.id and request.status == ReturnStatus.REFUNDED
            for request in order.returns
        )
        for order_item in order.items
    )
    if processed_total >= payment.amount:
        payment.payment_status = PaymentStatus.REFUNDED
        payment.refunded_at = utc_now()
        order.status = OrderStatus.REFUNDED
    elif all_items_refunded:
        order.status = OrderStatus.RETURNED
    else:
        order.status = OrderStatus.RETURN_REQUESTED


def apply_razorpay_refund_webhook(db: Session, payload: dict) -> ReturnRequest | None:
    event_name = payload.get("event")
    refund_entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
    refund_id = refund_entity.get("id")
    if not refund_id:
        return None

    return_request = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.refund_transaction_id == refund_id)
        .with_for_update()
        .first()
    )
    if return_request is None:
        return None

    import json

    return_request.refund_gateway_response = json.dumps(refund_entity)
    if event_name == "refund.processed":
        finalize_processed_refund(db, return_request)
    elif event_name == "refund.failed":
        return_request.status = ReturnStatus.REFUND_FAILED
        return_request.refund_error = (
            refund_entity.get("error_description")
            or refund_entity.get("error_reason")
            or "Razorpay refund failed"
        )[:500]
    else:
        return_request.status = ReturnStatus.REFUND_PENDING
    db.flush()
    return return_request

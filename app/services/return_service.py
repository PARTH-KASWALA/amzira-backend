from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.return_request import ReturnRequest

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

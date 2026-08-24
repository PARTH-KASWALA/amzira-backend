import json
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, require_admin
from app.core.rate_limiter import limiter
from app.db.session import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.payment import PaymentMethod, PaymentStatus
from app.models.return_request import ReturnRequest, ReturnStatus
from app.models.user import User
from app.schemas.return_request import ReturnRequestCreate
from app.services.return_service import (
    RETURN_STATUS_REQUESTED,
    get_existing_return_for_item,
    get_locked_order_for_return,
    refresh_return_status,
    finalize_processed_refund,
    restock_return_inventory,
    utc_now,
    validate_return_request,
)
from app.utils.response import success

router = APIRouter()


@router.post("/")
@limiter.limit("10/minute")
def create_return_request(
    request: Request,
    return_data: ReturnRequestCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Customer initiates return request for a delivered order item."""
    order = get_locked_order_for_return(
        db=db,
        order_id=return_data.order_id,
        user_id=current_user.id,
    )

    order_item = (
        db.query(OrderItem)
        .filter(
            OrderItem.id == return_data.order_item_id,
            OrderItem.order_id == order.id,
        )
        .with_for_update()
        .first()
    )
    if not order_item:
        raise HTTPException(status_code=404, detail="Order item not found")

    validate_return_request(order=order, order_item_id=order_item.id)

    existing_return = get_existing_return_for_item(db=db, order_item_id=order_item.id)
    if existing_return is not None:
        raise HTTPException(status_code=409, detail="Return request already exists for this item")

    return_request = ReturnRequest(
        order_id=return_data.order_id,
        order_item_id=return_data.order_item_id,
        user_id=current_user.id,
        reason=return_data.reason,
        description=return_data.description,
        refund_amount=order_item.total_price,
        refund_method="original_payment",
    )
    order.return_status = RETURN_STATUS_REQUESTED
    order.status = OrderStatus.RETURN_REQUESTED
    db.add(return_request)
    db.flush()
    db.commit()

    return success(
        data={"request_id": return_request.id},
        message="Return request submitted",
    )


@router.put("/{return_id}/approve")
def approve_return(
    return_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin approves return request."""
    return_request = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_request:
        raise HTTPException(status_code=404, detail="Return request not found")

    return_request.status = ReturnStatus.APPROVED
    db.commit()
    return success(message="Return approved")


@router.put("/{return_id}/refund")
def process_refund(
    return_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin processes refund after pickup confirmation."""
    return_request = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .with_for_update()
        .first()
    )
    if not return_request:
        raise HTTPException(status_code=404, detail="Return request not found")

    if return_request.status in {ReturnStatus.REFUND_PENDING, ReturnStatus.REFUNDED}:
        return success(
            data={
                "refund_id": return_request.refund_transaction_id,
                "refund_status": return_request.status.value,
            },
            message="Refund already initiated",
        )

    if return_request.status not in {ReturnStatus.PICKED_UP, ReturnStatus.REFUND_FAILED}:
        raise HTTPException(status_code=400, detail="Item not yet received")

    order = return_request.order
    payment = order.payment if order else None
    if payment is None or payment.payment_method != PaymentMethod.RAZORPAY:
        raise HTTPException(status_code=409, detail="This return requires a manual refund")
    if payment.payment_status not in {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED}:
        raise HTTPException(status_code=409, detail="Original payment is not eligible for refund")
    if not payment.razorpay_payment_id:
        raise HTTPException(status_code=409, detail="Razorpay payment reference is missing")

    refund_amount = Decimal(str(return_request.refund_amount or 0)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if refund_amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be positive")

    from app.services.payment_service import get_razorpay_client

    try:
        refund = get_razorpay_client().payment.refund(
            payment.razorpay_payment_id,
            {
                "amount": int(refund_amount * 100),
                "speed": "normal",
                "receipt": f"amzira-return-{return_request.id}",
                "notes": {
                    "return_request_id": str(return_request.id),
                    "order_number": order.order_number,
                },
            },
        )
    except Exception as exc:
        return_request.status = ReturnStatus.REFUND_FAILED
        return_request.refund_error = str(exc)[:500]
        db.commit()
        raise HTTPException(status_code=502, detail="Razorpay refund could not be initiated") from exc

    return_request.refund_transaction_id = refund.get("id")
    return_request.refund_gateway_response = json.dumps(refund)
    return_request.refund_error = None
    restock_return_inventory(db, return_request)
    if refund.get("status") == "processed":
        finalize_processed_refund(db, return_request)
    elif refund.get("status") == "failed":
        return_request.status = ReturnStatus.REFUND_FAILED
        return_request.refund_error = "Razorpay rejected the refund"
    else:
        return_request.status = ReturnStatus.REFUND_PENDING
    db.commit()
    return success(
        data={
            "refund_id": return_request.refund_transaction_id,
            "refund_status": return_request.status.value,
        },
        message="Refund initiated",
    )

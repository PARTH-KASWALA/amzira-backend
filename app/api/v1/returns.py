from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, require_admin
from app.core.rate_limiter import limiter
from app.db.session import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import ProductVariant
from app.models.return_request import ReturnRequest, ReturnStatus
from app.models.user import User
from app.schemas.return_request import ReturnRequestCreate
from app.services.return_service import (
    RETURN_STATUS_REQUESTED,
    get_existing_return_for_item,
    get_locked_order_for_return,
    refresh_return_status,
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
    return_request = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_request:
        raise HTTPException(status_code=404, detail="Return request not found")

    if return_request.status != ReturnStatus.PICKED_UP:
        raise HTTPException(status_code=400, detail="Item not yet received")

    return_request.status = ReturnStatus.REFUNDED
    return_request.resolved_at = utc_now()

    order_item = return_request.order_item
    variant = db.query(ProductVariant).filter(ProductVariant.id == order_item.variant_id).first()
    if variant:
        variant.stock_quantity += order_item.quantity

    if return_request.order:
        refresh_return_status(return_request.order)
        return_request.order.status = OrderStatus.RETURNED

    db.commit()
    return success(message="Refund processed")

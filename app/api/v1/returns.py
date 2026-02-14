from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, require_admin
from app.db.session import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import ProductVariant
from app.models.return_request import ReturnRequest, ReturnStatus
from app.models.user import User
from app.schemas.return_request import ReturnRequestCreate
from app.utils.response import success

router = APIRouter()


@router.post("/")
def create_return_request(
    return_data: ReturnRequestCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Customer initiates return request for a delivered order item."""
    order = db.query(Order).filter(
        Order.id == return_data.order_id,
        Order.user_id == current_user.id,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.DELIVERED:
        raise HTTPException(status_code=400, detail="Can only return delivered orders")

    days_since_delivery = (datetime.utcnow() - order.updated_at).days
    if days_since_delivery > 7:
        raise HTTPException(status_code=400, detail="Return window expired")

    order_item = db.query(OrderItem).filter(OrderItem.id == return_data.order_item_id).first()
    if not order_item:
        raise HTTPException(status_code=404, detail="Order item not found")

    return_request = ReturnRequest(
        order_id=return_data.order_id,
        order_item_id=return_data.order_item_id,
        reason=return_data.reason,
        description=return_data.description,
        refund_amount=order_item.total_price,
        refund_method="original_payment",
    )
    db.add(return_request)
    db.commit()

    return success(
        data={"request_id": str(return_request.id)},
        message="Return request submitted",
    )


@router.put("/{return_id}/approve")
def approve_return(
    return_id: str,
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
    return_id: str,
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
    return_request.resolved_at = datetime.utcnow()

    order_item = return_request.order_item
    variant = db.query(ProductVariant).filter(ProductVariant.id == order_item.variant_id).first()
    if variant:
        variant.stock_quantity += order_item.quantity

    db.commit()
    return success(message="Refund processed")

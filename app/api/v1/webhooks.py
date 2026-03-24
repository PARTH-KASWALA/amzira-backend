import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.order import Order, OrderStatus
from app.services.checkout_payment_service import create_order_from_checkout_webhook_payload
from app.services.payment_service import (
    cancel_payment_from_webhook,
    process_captured_payment_from_webhook,
    verify_razorpay_webhook_signature,
)
from app.services.shiprocket import fulfill_order
from app.services.return_service import mark_order_delivered
from app.utils.response import success

router = APIRouter()


def _normalize_shiprocket_status(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().upper().replace(" ", "_").replace("-", "_")


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    if not signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing signature")

    verify_razorpay_webhook_signature(body=body, signature=signature)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc

    event_name = payload.get("event")

    if event_name == "payment.captured":
        order = process_captured_payment_from_webhook(payload=payload, db=db)
        if order is None:
            order = create_order_from_checkout_webhook_payload(db=db, payload=payload)
            if order is not None:
                db.commit()
                db.refresh(order)
                try:
                    result = fulfill_order(order)
                    if (
                        result.get("fulfilled")
                        or result.get("shipment_id")
                        or result.get("shiprocket_order_id")
                        or result.get("awb_code")
                    ):
                        db.commit()
                except Exception:
                    db.rollback()
    elif event_name == "payment.failed":
        cancel_payment_from_webhook(payload=payload, db=db)

    return success(data={"status": "ok"}, message="Webhook processed")


@router.post("/shiprocket/webhook")
async def shiprocket_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    status_value = _normalize_shiprocket_status(
        payload.get("status")
        or payload.get("current_status")
        or payload.get("shipment_status")
        or payload.get("order_status")
    )
    shipment_id = payload.get("shipment_id")
    awb_code = payload.get("awb_code") or payload.get("awb")
    order_reference = payload.get("order_id") or payload.get("order_number")

    order_query = db.query(Order)
    order = None
    if shipment_id:
        order = order_query.filter(Order.shipment_id == str(shipment_id)).first()
    if order is None and awb_code:
        order = order_query.filter(Order.awb_code == str(awb_code)).first()
    if order is None and order_reference:
        order = order_query.filter(Order.order_number == str(order_reference)).first()

    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    order.current_location = payload.get("location") or payload.get("current_location") or order.current_location
    order.shiprocket_last_status = status_value or order.shiprocket_last_status
    order.courier_name = payload.get("courier_name") or order.courier_name
    order.tracking_url = payload.get("tracking_url") or order.tracking_url

    if status_value == "SHIPPED":
        order.status = OrderStatus.SHIPPED
    elif status_value == "OUT_FOR_DELIVERY":
        order.status = OrderStatus.OUT_FOR_DELIVERY
    elif status_value == "DELIVERED":
        mark_order_delivered(order)

    db.commit()
    return success(
        data={"order_id": order.id, "status": order.status.value, "shiprocket_status": status_value},
        message="Shiprocket webhook processed",
    )

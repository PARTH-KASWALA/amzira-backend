from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import settings
from app.models.order import Order, OrderStatus
from app.models.return_request import ReturnRequest, ReturnStatus
from app.services.return_service import mark_order_delivered

logger = logging.getLogger(__name__)

SHIPROCKET_TOKEN_CACHE_KEY = "shiprocket:auth_token"
_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": None}


@dataclass
class ShiprocketShipmentResult:
    shiprocket_order_id: str | None
    shipment_id: str | None
    awb_code: str | None
    courier_name: str | None
    tracking_url: str | None
    expected_delivery: datetime | None
    current_status: str | None
    current_location: str | None
    raw_response: dict[str, Any]


class ShiprocketConfigurationError(RuntimeError):
    pass


class ShiprocketAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    text = str(value).strip()
    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d %b %Y %H:%M",
    ):
        try:
            if fmt is None:
                return _ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
            return _ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _money_str(value: Any) -> str:
    return str(Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _bool_env_ready() -> bool:
    return bool((settings.SHIPROCKET_EMAIL or "").strip() and (settings.SHIPROCKET_PASSWORD or "").strip())


def validate_shiprocket_configuration(*, strict: bool = False) -> bool:
    ready = _bool_env_ready()
    if ready:
        return True
    message = "Shiprocket credentials are not configured"
    if strict:
        raise ShiprocketConfigurationError(message)
    logger.warning("shiprocket_configuration_missing")
    return False


def verify_shiprocket_webhook_signature(body: bytes, signature: str | None) -> None:
    secret = (settings.SHIPROCKET_WEBHOOK_SECRET or "").strip()
    if not secret:
        logger.error("shiprocket_webhook_secret_missing")
        raise ShiprocketAPIError("Shiprocket webhook secret is not configured")
    if not signature:
        raise ShiprocketAPIError("Missing Shiprocket webhook signature")

    generated_signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(generated_signature, signature):
        raise ShiprocketAPIError("Invalid Shiprocket webhook signature")


def _build_order_items(order: Order) -> tuple[list[dict[str, Any]], Decimal]:
    items: list[dict[str, Any]] = []
    total_weight = Decimal("0.00")
    for item in order.items:
        quantity = int(item.quantity or 0)
        unit_weight = Decimal(str(settings.SHIPROCKET_DEFAULT_WEIGHT_KG))
        total_weight += unit_weight * quantity
        items.append(
            {
                "name": item.product_name,
                "sku": getattr(item.variant, "sku", None) or f"ORDERITEM-{item.id}",
                "units": quantity,
                "selling_price": _money_str(item.unit_price),
                "discount": "0",
                "tax": "0",
            }
        )
    return items, total_weight


def _build_forward_order_payload(order: Order) -> dict[str, Any]:
    shipping = order.shipping_address
    billing = order.billing_address or shipping
    if shipping is None or billing is None:
        raise ShiprocketConfigurationError("Order addresses are required for Shiprocket")

    items, total_weight = _build_order_items(order)
    return {
        "order_id": order.order_number,
        "order_date": order.created_at.strftime("%Y-%m-%d %H:%M"),
        "pickup_location": settings.SHIPROCKET_PICKUP_LOCATION,
        "channel_id": settings.SHIPROCKET_CHANNEL_ID or None,
        "billing_customer_name": billing.full_name,
        "billing_last_name": "",
        "billing_address": billing.address_line1,
        "billing_address_2": billing.address_line2 or "",
        "billing_city": billing.city,
        "billing_pincode": billing.pincode,
        "billing_state": billing.state,
        "billing_country": billing.country or "India",
        "billing_email": order.user.email,
        "billing_phone": billing.phone,
        "shipping_is_billing": shipping.id == billing.id,
        "shipping_customer_name": shipping.full_name,
        "shipping_last_name": "",
        "shipping_address": shipping.address_line1,
        "shipping_address_2": shipping.address_line2 or "",
        "shipping_city": shipping.city,
        "shipping_pincode": shipping.pincode,
        "shipping_country": shipping.country or "India",
        "shipping_state": shipping.state,
        "shipping_email": order.user.email,
        "shipping_phone": shipping.phone,
        "order_items": items,
        "payment_method": "COD" if order.payment and getattr(order.payment.payment_method, "value", "") == "cod" else "Prepaid",
        "sub_total": _money_str(order.subtotal),
        "shipping_charges": _money_str(order.shipping_charge),
        "total_discount": _money_str(order.discount_amount),
        "total": _money_str(order.total_amount),
        "weight": float(total_weight or Decimal(str(settings.SHIPROCKET_DEFAULT_WEIGHT_KG))),
        "length": settings.SHIPROCKET_DEFAULT_LENGTH_CM,
        "breadth": settings.SHIPROCKET_DEFAULT_BREADTH_CM,
        "height": settings.SHIPROCKET_DEFAULT_HEIGHT_CM,
    }


def _build_return_payload(order: Order, return_request: ReturnRequest) -> dict[str, Any]:
    order_item = return_request.order_item
    shipping = order.shipping_address
    if shipping is None or order_item is None:
        raise ShiprocketConfigurationError("Return shipments require shipping address and order item")

    return {
        "order_id": f"RET-{order.order_number}-{return_request.id}",
        "order_date": _utc_now().strftime("%Y-%m-%d %H:%M"),
        "pickup_location": settings.SHIPROCKET_PICKUP_LOCATION,
        "channel_id": settings.SHIPROCKET_CHANNEL_ID or None,
        "billing_customer_name": shipping.full_name,
        "billing_last_name": "",
        "billing_address": shipping.address_line1,
        "billing_address_2": shipping.address_line2 or "",
        "billing_city": shipping.city,
        "billing_pincode": shipping.pincode,
        "billing_state": shipping.state,
        "billing_country": shipping.country or "India",
        "billing_email": order.user.email,
        "billing_phone": shipping.phone,
        "shipping_is_billing": True,
        "shipping_customer_name": shipping.full_name,
        "shipping_last_name": "",
        "shipping_address": shipping.address_line1,
        "shipping_address_2": shipping.address_line2 or "",
        "shipping_city": shipping.city,
        "shipping_pincode": shipping.pincode,
        "shipping_country": shipping.country or "India",
        "shipping_state": shipping.state,
        "shipping_email": order.user.email,
        "shipping_phone": shipping.phone,
        "order_items": [
            {
                "name": order_item.product_name,
                "sku": getattr(order_item.variant, "sku", None) or f"RETURNITEM-{order_item.id}",
                "units": int(order_item.quantity),
                "selling_price": _money_str(order_item.unit_price),
            }
        ],
        "payment_method": "Prepaid",
        "sub_total": _money_str(order_item.total_price),
        "weight": float(Decimal(str(settings.SHIPROCKET_DEFAULT_WEIGHT_KG)) * int(order_item.quantity)),
        "length": settings.SHIPROCKET_DEFAULT_LENGTH_CM,
        "breadth": settings.SHIPROCKET_DEFAULT_BREADTH_CM,
        "height": settings.SHIPROCKET_DEFAULT_HEIGHT_CM,
    }


def _extract_tracking_fields(payload: dict[str, Any]) -> ShiprocketShipmentResult:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tracking_url = (
        data.get("tracking_url")
        or data.get("awb_track_url")
        or payload.get("tracking_url")
        or payload.get("awb_track_url")
    )
    expected_delivery = _parse_datetime(
        data.get("expected_delivery_date")
        or data.get("etd")
        or payload.get("expected_delivery_date")
        or payload.get("etd")
    )
    current_location = (
        data.get("current_location")
        or data.get("current_status_location")
        or payload.get("current_location")
    )
    current_status = (
        data.get("shipment_status")
        or data.get("current_status")
        or payload.get("shipment_status")
        or payload.get("current_status")
    )
    return ShiprocketShipmentResult(
        shiprocket_order_id=str(data.get("order_id") or payload.get("order_id")) if data.get("order_id") or payload.get("order_id") else None,
        shipment_id=str(data.get("shipment_id") or payload.get("shipment_id")) if data.get("shipment_id") or payload.get("shipment_id") else None,
        awb_code=str(data.get("awb_code") or payload.get("awb_code")) if data.get("awb_code") or payload.get("awb_code") else None,
        courier_name=data.get("courier_name") or data.get("courier_company_name") or payload.get("courier_name"),
        tracking_url=tracking_url,
        expected_delivery=expected_delivery,
        current_status=str(current_status) if current_status is not None else None,
        current_location=current_location,
        raw_response=payload,
    )


def _memory_token_valid() -> bool:
    expires_at = _TOKEN_CACHE.get("expires_at")
    return bool(_TOKEN_CACHE.get("token") and isinstance(expires_at, datetime) and expires_at > _utc_now())


def get_token(force_refresh: bool = False) -> str:
    if not _bool_env_ready():
        raise ShiprocketConfigurationError("Shiprocket credentials are not configured")

    if not force_refresh and _memory_token_valid():
        return _TOKEN_CACHE["token"]

    if not force_refresh:
        cached = cache_get_json(SHIPROCKET_TOKEN_CACHE_KEY)
        if isinstance(cached, dict):
            token = cached.get("token")
            expires_at = _parse_datetime(cached.get("expires_at"))
            if token and expires_at and expires_at > _utc_now():
                _TOKEN_CACHE["token"] = token
                _TOKEN_CACHE["expires_at"] = expires_at
                return token

    auth_payload = {
        "email": settings.SHIPROCKET_EMAIL,
        "password": settings.SHIPROCKET_PASSWORD,
    }
    with httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        response = client.post(f"{settings.SHIPROCKET_BASE_URL}/auth/login", json=auth_payload)
    if response.status_code >= 400:
        logger.error("shiprocket_auth_failed status_code=%s body=%s", response.status_code, response.text)
        raise ShiprocketAPIError("Shiprocket authentication failed", status_code=response.status_code)

    payload = response.json()
    token = payload.get("token")
    if not token:
        raise ShiprocketAPIError("Shiprocket authentication response missing token", payload=payload)

    expires_at = _utc_now() + timedelta(seconds=settings.SHIPROCKET_TOKEN_TTL_SECONDS)
    cache_payload = {"token": token, "expires_at": expires_at.isoformat()}
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = expires_at
    cache_set_json(SHIPROCKET_TOKEN_CACHE_KEY, cache_payload, settings.SHIPROCKET_TOKEN_TTL_SECONDS)
    return token


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    allow_auth_retry: bool = True,
) -> dict[str, Any]:
    retries = max(1, int(settings.SHIPROCKET_MAX_RETRIES))
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        token = get_token(force_refresh=attempt > 1 and allow_auth_retry)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = client.request(
                    method,
                    f"{settings.SHIPROCKET_BASE_URL}{path}",
                    json=json_body,
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("shiprocket_http_error attempt=%s path=%s error=%s", attempt, path, str(exc))
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 5))
                continue
            raise ShiprocketAPIError("Shiprocket request failed") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        logger.info(
            "shiprocket_api_call path=%s method=%s status_code=%s response=%s",
            path,
            method,
            response.status_code,
            payload,
        )

        if response.status_code == 401 and allow_auth_retry and attempt < retries:
            _TOKEN_CACHE["token"] = None
            _TOKEN_CACHE["expires_at"] = None
            continue

        if response.status_code >= 400:
            last_error = ShiprocketAPIError(
                payload.get("message") or f"Shiprocket API failed for {path}",
                status_code=response.status_code,
                payload=payload if isinstance(payload, dict) else {},
            )
            if response.status_code >= 500 and attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 5))
                continue
            raise last_error

        if isinstance(payload, dict):
            return payload
        return {"data": payload}

    if isinstance(last_error, ShiprocketAPIError):
        raise last_error
    raise ShiprocketAPIError("Shiprocket request failed")


def create_forward_shipment(order: Order) -> ShiprocketShipmentResult:
    payload = _request("POST", "/orders/create/adhoc", json_body=_build_forward_order_payload(order))
    return _extract_tracking_fields(payload)


def assign_awb(order: Order) -> ShiprocketShipmentResult:
    if not order.shipment_id:
        raise ShiprocketConfigurationError("Shipment id is required before AWB assignment")
    payload = _request("POST", "/courier/assign/awb", json_body={"shipment_id": order.shipment_id})
    return _extract_tracking_fields(payload)


def generate_pickup(order: Order) -> dict[str, Any]:
    if not order.shipment_id:
        raise ShiprocketConfigurationError("Shipment id is required before pickup generation")
    return _request("POST", "/courier/generate/pickup", json_body={"shipment_id": [order.shipment_id]})


def track_awb(awb_code: str) -> ShiprocketShipmentResult:
    payload = _request("GET", f"/courier/track/awb/{awb_code}")
    tracking_data = payload.get("tracking_data") if isinstance(payload.get("tracking_data"), dict) else payload.get("data")
    envelope = tracking_data if isinstance(tracking_data, dict) else payload

    shipment_track = envelope.get("shipment_track") if isinstance(envelope.get("shipment_track"), list) and envelope.get("shipment_track") else None
    track_entry = shipment_track[0] if shipment_track else {}
    shipment_track_activities = envelope.get("shipment_track_activities") or []
    latest_activity = shipment_track_activities[0] if shipment_track_activities else {}

    normalized_payload = {
        "order_id": track_entry.get("order_id") or envelope.get("order_id"),
        "shipment_id": track_entry.get("shipment_id") or envelope.get("shipment_id"),
        "awb_code": awb_code,
        "courier_name": track_entry.get("courier_name") or envelope.get("courier_name"),
        "tracking_url": track_entry.get("tracking_url") or track_entry.get("track_url"),
        "expected_delivery_date": track_entry.get("edd") or envelope.get("expected_delivery_date"),
        "shipment_status": latest_activity.get("sr-status-label") or latest_activity.get("activity") or track_entry.get("current_status"),
        "current_location": latest_activity.get("location") or track_entry.get("current_status_location"),
    }
    return _extract_tracking_fields({"data": normalized_payload, "raw": payload})


def create_return_shipment(order: Order, return_request: ReturnRequest) -> ShiprocketShipmentResult:
    shipping = order.shipping_address
    if shipping and shipping.pincode:
        check_pincode_serviceability(shipping.pincode, cod=False)
    payload = _request("POST", "/orders/create/return", json_body=_build_return_payload(order, return_request))
    return _extract_tracking_fields(payload)


def sync_order_tracking(order: Order) -> ShiprocketShipmentResult | None:
    if not order.awb_code:
        return None
    tracking = track_awb(order.awb_code)
    apply_tracking_update(order, tracking)
    return tracking


def check_pincode_serviceability(pincode: str, *, cod: bool = False) -> dict[str, Any]:
    normalized_pincode = str(pincode or "").strip()
    if not normalized_pincode:
        raise ShiprocketConfigurationError("Delivery pincode is required for Shiprocket serviceability checks")

    pickup_postcode = str(settings.SHIPROCKET_PICKUP_POSTCODE or "").strip()
    if not pickup_postcode:
        logger.warning("shiprocket_pickup_postcode_missing")
        return {"serviceable": True, "skipped": True, "reason": "pickup_postcode_missing"}

    payload = _request(
        "GET",
        "/courier/serviceability/",
        params={
            "pickup_postcode": pickup_postcode,
            "delivery_postcode": normalized_pincode,
            "cod": 1 if cod else 0,
            "weight": settings.SHIPROCKET_DEFAULT_WEIGHT_KG,
        },
    )

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    available_couriers = data.get("available_courier_companies")
    if isinstance(available_couriers, list):
        serviceable = len(available_couriers) > 0
    else:
        serviceable = bool(data.get("serviceable", payload.get("serviceable", False)))

    if not serviceable:
        raise ShiprocketAPIError(
            f"Delivery pincode {normalized_pincode} is not serviceable",
            payload=payload if isinstance(payload, dict) else {},
        )

    return {"serviceable": True, "skipped": False, "payload": payload}


def apply_tracking_update(order: Order, tracking: ShiprocketShipmentResult) -> None:
    if tracking.shiprocket_order_id:
        order.shiprocket_order_id = tracking.shiprocket_order_id
    if tracking.shipment_id:
        order.shipment_id = tracking.shipment_id
        order.tracking_id = tracking.shipment_id
    if tracking.awb_code:
        order.awb_code = tracking.awb_code
        order.tracking_number = tracking.awb_code
    if tracking.courier_name:
        order.courier_name = tracking.courier_name
        order.carrier_name = tracking.courier_name
    if tracking.tracking_url:
        order.tracking_url = tracking.tracking_url
    if tracking.current_location:
        order.current_location = tracking.current_location
    if tracking.expected_delivery:
        order.estimated_delivery_date = tracking.expected_delivery
    if tracking.current_status:
        normalized = str(tracking.current_status).strip().upper().replace(" ", "_").replace("-", "_")
        order.shiprocket_last_status = normalized
        order.courier_status = normalized
        if normalized == "SHIPPED":
            order.status = OrderStatus.SHIPPED
        elif normalized == "OUT_FOR_DELIVERY":
            order.status = OrderStatus.OUT_FOR_DELIVERY
        elif normalized == "DELIVERED":
            order.delivery_date = tracking.expected_delivery or _utc_now()
            mark_order_delivered(order)


def fulfill_order(order: Order) -> dict[str, Any]:
    if not _bool_env_ready():
        logger.error("shiprocket_skipped order_id=%s reason=missing_configuration", order.id)
        raise ShiprocketConfigurationError("Shiprocket credentials are not configured")

    if order.shipment_id and order.awb_code:
        return {"fulfilled": False, "reason": "already_fulfilled"}

    shipping = order.shipping_address
    if shipping and shipping.pincode:
        check_pincode_serviceability(
            shipping.pincode,
            cod=bool(order.payment and getattr(order.payment.payment_method, "value", "") == "cod"),
        )

    created = create_forward_shipment(order)
    apply_tracking_update(order, created)
    result: dict[str, Any] = {
        "fulfilled": False,
        "shiprocket_order_id": order.shiprocket_order_id,
        "shipment_id": order.shipment_id,
        "awb_code": order.awb_code,
        "pickup": None,
        "tracking": None,
    }

    try:
        awb = assign_awb(order)
        apply_tracking_update(order, awb)
        result["awb_code"] = order.awb_code
    except Exception as exc:
        logger.exception("shiprocket_awb_assignment_failed order_id=%s shipment_id=%s", order.id, order.shipment_id)
        result["error"] = str(exc)
        raise

    try:
        pickup_payload = generate_pickup(order)
        order.pickup_scheduled_at = _utc_now()
        result["pickup"] = pickup_payload
    except Exception as exc:
        logger.exception("shiprocket_pickup_generation_failed order_id=%s shipment_id=%s", order.id, order.shipment_id)
        result["error"] = str(exc)
        raise

    try:
        tracking = sync_order_tracking(order)
        result["tracking"] = tracking.raw_response if tracking else None
    except Exception:
        logger.exception("shiprocket_tracking_sync_failed order_id=%s awb_code=%s", order.id, order.awb_code)

    result["fulfilled"] = bool(order.shipment_id and order.awb_code)
    result["shiprocket_order_id"] = order.shiprocket_order_id
    result["shipment_id"] = order.shipment_id
    result["awb_code"] = order.awb_code
    return result


def apply_return_tracking_update(return_request: ReturnRequest, payload: dict[str, Any]) -> ReturnRequest:
    status_value = (
        payload.get("status")
        or payload.get("current_status")
        or payload.get("shipment_status")
        or payload.get("order_status")
    )
    normalized = str(status_value).strip().upper().replace(" ", "_").replace("-", "_") if status_value else None

    return_request.return_courier_name = payload.get("courier_name") or return_request.return_courier_name
    return_request.return_tracking_url = payload.get("tracking_url") or return_request.return_tracking_url
    return_request.return_awb_code = payload.get("awb_code") or payload.get("awb") or return_request.return_awb_code

    if normalized in {"PICKED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED"}:
        return_request.status = ReturnStatus.PICKED_UP
    return return_request

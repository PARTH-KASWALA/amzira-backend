from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.core.rate_limiter import limiter
from app.db.session import get_db
from app.models.cart import CartItem
from app.models.product import ProductVariant
from app.schemas.stock import InsufficientStockItem, StockCheckRequest, StockCheckResponse
from app.utils.response import success

router = APIRouter()


def _build_stock_response(payload: StockCheckRequest, db: Session) -> dict:
    requested_quantities: dict[int, int] = {}
    for item in payload.items:
        requested_quantities[item.variant_id] = requested_quantities.get(item.variant_id, 0) + item.quantity

    insufficient_items: list[InsufficientStockItem] = []

    if requested_quantities:
        locked_variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.id.in_(sorted(requested_quantities.keys())))
            .with_for_update()
            .all()
        )
        variants_by_id = {variant.id: variant for variant in locked_variants}

        for variant_id, requested_quantity in requested_quantities.items():
            variant = variants_by_id.get(variant_id)
            available_quantity = variant.stock_quantity if variant else 0
            if available_quantity < requested_quantity:
                insufficient_items.append(
                    InsufficientStockItem(
                        variant_id=variant_id,
                        available_quantity=available_quantity,
                        requested_quantity=requested_quantity,
                        message=f"Insufficient stock for variant {variant_id}",
                    )
                )

    response = StockCheckResponse(
        available=len(insufficient_items) == 0,
        items=insufficient_items,
        insufficient_items=insufficient_items,
    )
    return success(data=response.model_dump())


@router.post("/check", response_model=dict)
@limiter.limit("120/minute")
def check_stock(
    request: Request,
    payload: StockCheckRequest,
    db: Session = Depends(get_db),
):
    """Check stock availability for variant quantities without deducting inventory."""
    return _build_stock_response(payload, db)


@router.get("/check", response_model=dict)
@limiter.limit("120/minute")
def check_stock_legacy(
    request: Request,
    variant_id: list[int] | None = Query(default=None),
    quantity: list[int] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Backward-compatible stock check endpoint.
    Accepts repeated query params: ?variant_id=1&quantity=2&variant_id=4&quantity=1
    """
    variant_ids = variant_id or []
    quantities = quantity or []

    if not variant_ids and not quantities:
        access_token = request.cookies.get("access_token")
        if not access_token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                access_token = auth_header.split(" ", 1)[1]

        if access_token:
            try:
                decoded = decode_token(access_token)
                user_id = int(decoded.get("sub"))
                cart_items = db.query(CartItem).filter(CartItem.user_id == user_id).all()
                items = [
                    {"variant_id": item.variant_id, "quantity": item.quantity}
                    for item in cart_items
                ]
                payload = StockCheckRequest.model_validate({"items": items})
                return _build_stock_response(payload, db)
            except Exception:
                pass

        raise HTTPException(
            status_code=400,
            detail="items payload is required. Use POST /api/v1/stock/check",
        )

    if len(variant_ids) != len(quantities):
        raise HTTPException(
            status_code=400,
            detail="variant_id and quantity must be provided in matching counts",
        )

    items = [
        {"variant_id": variant_ids[idx], "quantity": quantities[idx]}
        for idx in range(len(variant_ids))
    ]
    payload = StockCheckRequest.model_validate({"items": items})
    return _build_stock_response(payload, db)

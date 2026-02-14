from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from typing import Any, Optional, Dict


def success(
    data: Optional[Any] = None,
    message: str = "Success",
    meta: Optional[Dict] = None,
):
    response = {
        "success": True,
        "message": message,
        "data": data,
        "errors": None,
    }

    if meta is not None:
        response["meta"] = meta

    # Ensure SQLAlchemy models, datetimes, Decimals, etc. are JSON-serializable.
    return jsonable_encoder(response)


def error(
    message: str = "Error",
    errors: Optional[Any] = None,
    status_code: int = 400,
):
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "message": message,
            "data": None,
            "errors": errors,
        },
    )


def paginated_response(
    items,
    total: int,
    page: int,
    limit: int,
):
    return success(
        data=items,
        meta={
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        },
    )

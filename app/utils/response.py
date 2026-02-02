# app/utils/response.py

def success(data=None, message="Success"):
    return {
        "success": True,
        "data": data,
        "message": message,
        "errors": None
    }

# def error(message="Error", errors=None):
#     return {
#         "success": False,
#         "data": None,
#         "message": message,
#         "errors": errors
#     }


def error(message="Error", errors=None, status_code=400):
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "message": message,
            "errors": errors
        }
    )


# Pagination meta
def paginated_response(items, total, page, limit):
    return success(
        data=items,
        meta={
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    )
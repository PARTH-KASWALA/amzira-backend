# app/utils/response.py

def success(data=None, message="Success"):
    return {
        "success": True,
        "data": data,
        "message": message,
        "errors": None
    }

def error(message="Error", errors=None):
    return {
        "success": False,
        "data": None,
        "message": message,
        "errors": errors
    }

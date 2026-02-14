from fastapi import HTTPException, status
from typing import Any, List, Optional


class EmailAlreadyExists(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )


class ProductNotFound(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )


class InsufficientStock(HTTPException):
    def __init__(self, available: int):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock. Only {available} items available"
        )


class InvalidCredentials(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )


class OrderNotFound(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )


class APIError(Exception):
    def __init__(self, status_code: int, message: str, errors: Optional[List[Any]] = None):
        self.status_code = status_code
        self.message = message
        self.errors = errors or []

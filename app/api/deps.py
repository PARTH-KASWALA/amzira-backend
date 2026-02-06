from fastapi import Request,Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole

security = HTTPBearer()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user.
    Supports:
    - httpOnly cookie auth (preferred)
    - Authorization header (fallback for Postman/dev)
    """

    token = None

    # 1️⃣ Try httpOnly cookie first (PRODUCTION PATH)
    if request.cookies.get("access_token"):
        token = request.cookies.get("access_token")

    # 2️⃣ Fallback to Authorization header (Postman/dev)
    elif request.headers.get("Authorization"):
        auth_header = request.headers.get("Authorization")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    # 3️⃣ No token → not authenticated
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    # 4️⃣ Decode JWT
    payload = decode_token(token)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )

    # 5️⃣ Load user
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # 6️⃣ Active check
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive"
        )

    return user



def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Ensure user is active"""
    return current_user


# def require_admin(
#     current_user: User = Depends(get_current_user)
# ) -> User:
#     """Require admin role"""
#     if current_user.role != UserRole.ADMIN:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Admin access required"
#         )
#     return current_user





from fastapi import Depends, HTTPException, Request
import structlog

from app.core.config import settings
from app.models.user import User, UserRole
from app.api.deps import get_current_user

logger = structlog.get_logger()


# app/api/deps.py
# def require_admin(
#     request: Request,
#     current_user: User = Depends(get_current_user)
# ) -> User:
#     if current_user.role != UserRole.ADMIN:
#         raise HTTPException(status_code=403, detail="Admin access required")
    
#     # IP whitelist in production
#     if settings.ENVIRONMENT == "production":
#         client_ip = request.client.host
#         if client_ip not in settings.ADMIN_ALLOWED_IPS:
#             logger.warning("admin_access_denied", ip=client_ip, user=current_user.email)
#             raise HTTPException(status_code=403, detail="Access denied")
    
#     return current_user


def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    # IP whitelist in production
    if settings.ENVIRONMENT == "production":
        client_ip = request.client.host if request.client else None

        if client_ip not in settings.ADMIN_ALLOWED_IPS:
            logger.warning(
                "admin_access_denied",
                ip=client_ip,
                user_email=current_user.email,
            )
            raise HTTPException(
                status_code=403,
                detail="Access denied",
            )

    return current_user

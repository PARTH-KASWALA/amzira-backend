from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token"""
    token = credentials.credentials
    payload = decode_token(token)
    
    user_id: int = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
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
    """
    Dependency to ensure the user is an admin.
    In production, also enforces IP whitelist.
    """

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

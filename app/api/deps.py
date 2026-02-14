import structlog
import ipaddress
from datetime import datetime
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User, UserRole

logger = structlog.get_logger()


def _is_token_revoked(db: Session, jti: str) -> bool:
    if not jti:
        return True
    return (
        db.query(TokenBlacklist)
        .filter(
            TokenBlacklist.jti == jti,
            TokenBlacklist.expires_at > datetime.utcnow(),
        )
        .first()
        is not None
    )


def get_real_client_ip(request: Request) -> tuple[str | None, list[str]]:
    """Return client IP and full proxy chain if provided."""
    direct_ip = request.client.host if request.client else None
    trust_proxy_headers = (
        settings.ENVIRONMENT.lower() == "production"
        and settings.TRUST_PROXY_HEADERS
        and settings.is_trusted_proxy(direct_ip)
    )

    if not trust_proxy_headers:
        return direct_ip, []

    chain: list[str] = []
    cf_connecting_ip = request.headers.get("CF-Connecting-IP")
    if cf_connecting_ip:
        candidate = cf_connecting_ip.strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate, [candidate]
        except ValueError:
            pass

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        chain = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]

    # RFC7239 fallback
    forwarded = request.headers.get("Forwarded")
    if forwarded and not chain:
        parts = [segment.strip() for segment in forwarded.split(";")]
        for part in parts:
            if part.lower().startswith("for="):
                candidate = part.split("=", 1)[1].strip().strip('"')
                chain.append(candidate)

    if chain:
        for candidate in chain:
            try:
                ipaddress.ip_address(candidate)
                return candidate, chain
            except ValueError:
                continue

    return direct_ip, chain


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Get current authenticated user from cookie or bearer token."""
    token = None

    if request.cookies.get("access_token"):
        token = request.cookies.get("access_token")
    elif request.headers.get("Authorization"):
        auth_header = request.headers.get("Authorization")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_token(token)
    jti = payload.get("jti")
    if _is_token_revoked(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    raw_session_version = payload.get("session_version", 0)
    try:
        token_session_version = int(raw_session_version)
    except (TypeError, ValueError):
        token_session_version = -1
    if token_session_version != user.session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been invalidated. Please login again.",
        )

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    client_ip, ip_chain = get_real_client_ip(request)
    action_name = f"{request.method} {request.url.path}"

    if settings.ENVIRONMENT.lower() == "production" and client_ip not in settings.admin_allowed_ips:
        logger.warning(
            "admin_access_denied",
            action=action_name,
            admin_user_id=current_user.id,
            client_ip=client_ip,
            ip_chain=ip_chain,
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    logger.info(
        "admin_action",
        action=action_name,
        admin_user_id=current_user.id,
        client_ip=client_ip,
        ip_chain=ip_chain,
    )
    return current_user

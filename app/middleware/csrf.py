from secrets import token_urlsafe
import hmac
from urllib.parse import urlparse

from fastapi import Request, Response

from app.core.config import settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_MAX_AGE = 60 * 60 * 24


def _csrf_cookie_domain(request: Request | None = None) -> str | None:
    """Make the browser-readable CSRF cookie available to the storefront."""
    if settings.ENVIRONMENT != "production":
        return None

    request_host = (request.url.hostname or "").lower() if request else ""
    if request_host and request_host != "amzira.com" and not request_host.endswith(".amzira.com"):
        return None

    frontend_host = (urlparse(settings.FRONTEND_URL).hostname or "").lower()
    if frontend_host == "amzira.com" or frontend_host.endswith(".amzira.com"):
        return "amzira.com"
    return None


def generate_csrf_token() -> str:
    return token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, request: Request | None = None) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=CSRF_COOKIE_MAX_AGE,
        path="/",
        domain=_csrf_cookie_domain(request),
    )


def verify_csrf_token(request: Request) -> bool:
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)

    if not csrf_cookie or not csrf_header:
        return False

    return hmac.compare_digest(csrf_cookie, csrf_header)

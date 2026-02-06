from secrets import token_urlsafe
import hmac

from fastapi import Request, Response

from app.core.config import settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_MAX_AGE = 60 * 60 * 24


def generate_csrf_token() -> str:
    return token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=CSRF_COOKIE_MAX_AGE,
        path="/",
    )


def verify_csrf_token(request: Request) -> bool:
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)

    if not csrf_cookie or not csrf_header:
        return False

    return hmac.compare_digest(csrf_cookie, csrf_header)

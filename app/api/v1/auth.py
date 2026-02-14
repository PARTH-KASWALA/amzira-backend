from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import EmailAlreadyExists, InvalidCredentials
from app.core.rate_limiter import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.middleware.csrf import CSRF_COOKIE_NAME, generate_csrf_token, set_csrf_cookie
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User
from app.tasks.email_tasks import send_password_reset
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.utils.response import success

router = APIRouter()


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


def _blacklist_token(db: Session, token: str, reason: str) -> None:
    payload = decode_token(token)
    jti = payload.get("jti")
    user_id = payload.get("sub")
    exp = payload.get("exp")
    if not jti or not user_id or not exp:
        return

    expires_at = datetime.utcfromtimestamp(exp)
    existing = db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first()
    if existing:
        return

    db.add(
        TokenBlacklist(
            jti=jti,
            user_id=int(user_id),
            expires_at=expires_at,
            reason=reason,
        )
    )


def _create_password_reset_token(email: str, expires_minutes: int = 30) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {
        "sub": email,
        "type": "password_reset",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _should_use_secure_cookies(request: Request) -> bool:
    if settings.ENVIRONMENT != "production":
        return False
    return request.url.scheme == "https"


def _set_auth_cookies(
    response: JSONResponse,
    access_token: str,
    refresh_token: str,
    request: Request,
) -> None:
    secure = _should_use_secure_cookies(request)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )


@router.get("/csrf-token")
def get_csrf_token():
    token = generate_csrf_token()
    response = JSONResponse(content=success(message="CSRF token set"))
    set_csrf_cookie(response, token)
    return response


@router.post(
    "/register",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Register user account",
    description="""
Creates a new user account and returns standardized response payload.

Validation:
1. Email must be unique
2. Optional phone must be unique
3. Password is hashed before persistence
""",
    responses={
        201: {"description": "Registration successful"},
        409: {"description": "Email or phone already registered"},
        422: {"description": "Validation error"},
    },
    tags=["Authentication"],
)
@limiter.limit("5/minute")
def register(request: Request, user_in: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user_in.email).first()
    if existing_user:
        raise EmailAlreadyExists()

    if user_in.phone:
        existing_phone = db.query(User).filter(User.phone == user_in.phone).first()
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already registered",
            )

    user = User(
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        full_name=user_in.full_name,
        phone=user_in.phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return success(
        data={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "phone": user.phone,
            "role": user.role.value,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
        },
        message="Registration successful",
    )


@router.post(
    "/login",
    response_model=dict,
    summary="Login user",
    description="""
Authenticates a user and sets `access_token` and `refresh_token` as httpOnly cookies.

Behavior:
1. Validates credentials
2. Ensures user is active
3. Issues JWT tokens
4. Sets secure cookie attributes based on environment
""",
    responses={
        200: {"description": "Login successful"},
        401: {"description": "Invalid credentials"},
        403: {"description": "Account inactive"},
    },
    tags=["Authentication"],
)
@limiter.limit("5/minute")
async def login(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    credentials = UserLogin(**payload)

    user = db.query(User).filter(User.email == credentials.email).first()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise InvalidCredentials()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    # Rotate session version to invalidate all previously issued tokens.
    # In dev, keep session_version stable so Postman logins don't invalidate browser sessions.
    if settings.ENVIRONMENT.lower() == "production":
        user.session_version += 1
        db.commit()
        db.refresh(user)

    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "role": user.role.value,
            "session_version": user.session_version,
        }
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id), "session_version": user.session_version}
    )

    response = JSONResponse(
        content=success(
            data={
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role.value,
                },
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
            message="Login successful",
        )
    )
    _set_auth_cookies(response, access_token, refresh_token, request)
    return response


@router.post("/refresh")
@limiter.limit("20/minute")
def refresh_token(request: Request, db: Session = Depends(get_db)):
    refresh_token_value = request.cookies.get("refresh_token")
    if not refresh_token_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found",
        )

    payload = decode_token(refresh_token_value)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    blacklisted = (
        db.query(TokenBlacklist)
        .filter(
            TokenBlacklist.jti == jti,
            TokenBlacklist.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
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

    new_access_token = create_access_token(
        data={
            "sub": str(user.id),
            "role": user.role.value,
            "session_version": user.session_version,
        }
    )
    response = JSONResponse(content=success(message="Token refreshed"))
    secure = _should_use_secure_cookies(request)
    response.set_cookie(
        key="access_token",
        value=new_access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    access_token = request.cookies.get("access_token")
    refresh_token_value = request.cookies.get("refresh_token")

    for token in [access_token, refresh_token_value]:
        if not token:
            continue
        try:
            _blacklist_token(db, token, reason="logout")
        except HTTPException:
            continue

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

    response = JSONResponse(content=success(message="Logout successful"))
    secure = _should_use_secure_cookies(request)
    response.delete_cookie(key="access_token", path="/", samesite="lax", secure=secure)
    response.delete_cookie(key="refresh_token", path="/", samesite="lax", secure=secure)
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", samesite="lax", secure=secure)
    return response


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == payload.email).first()

    if user and user.is_active:
        token = _create_password_reset_token(user.email)
        try:
            send_password_reset.delay(user.email, token)
        except Exception:
            # Intentionally avoid leaking internals to callers.
            pass

    return success(
        message="If an account exists, password reset instructions have been sent.",
    )

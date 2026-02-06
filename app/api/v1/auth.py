from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import EmailAlreadyExists, InvalidCredentials
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.middleware.csrf import CSRF_COOKIE_NAME, generate_csrf_token, set_csrf_cookie
from app.models.user import User
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.utils.response import success

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


def _set_auth_cookies(response: JSONResponse, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
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


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
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

    return success(data=user, message="Registration successful")


@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
def login(request: Request, credentials: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == credentials.email).first()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise InvalidCredentials()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    access_token = create_access_token(data={"sub": str(user.id), "role": user.role.value})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})

    response = JSONResponse(
        content=success(
            data={
                "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role.value,
                }
            },
            message="Login successful",
        )
    )
    _set_auth_cookies(response, access_token, refresh_token)
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

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    new_access_token = create_access_token(data={"sub": str(user.id), "role": user.role.value})
    response = JSONResponse(content=success(message="Token refreshed"))
    response.set_cookie(
        key="access_token",
        value=new_access_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return response


@router.post("/logout")
def logout():
    response = JSONResponse(content=success(message="Logout successful"))
    secure = settings.ENVIRONMENT == "production"
    response.delete_cookie(key="access_token", path="/", samesite="lax", secure=secure)
    response.delete_cookie(key="refresh_token", path="/", samesite="lax", secure=secure)
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", samesite="lax", secure=secure)
    return response

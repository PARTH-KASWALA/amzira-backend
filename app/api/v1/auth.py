from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import timedelta
from app.db.session import get_db
from app.models.user import User
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserResponse, UserLogin
from app.core.security import verify_password, hash_password, create_access_token, create_refresh_token
from app.core.exceptions import EmailAlreadyExists, InvalidCredentials
from app.core.config import settings
from fastapi import Request


# Rate limiting for auth endpoints
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request,user_in: UserCreate, db: Session = Depends(get_db)):
    """Register new user"""
    # Check if email exists
    existing_user = db.query(User).filter(User.email == user_in.email).first()
    if existing_user:
        raise EmailAlreadyExists()
    
    # Check if phone exists
    if user_in.phone:
        existing_phone = db.query(User).filter(User.phone == user_in.phone).first()
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already registered"
            )
    
    # Create user
    user = User(
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        full_name=user_in.full_name,
        phone=user_in.phone
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return user


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(request: Request,credentials: UserLogin, db: Session = Depends(get_db)):
    """User login"""
    user = db.query(User).filter(User.email == credentials.email).first()
    
    if not user or not verify_password(credentials.password, user.password_hash):
        raise InvalidCredentials()
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive"
        )
    
    # Create tokens
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value}
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id)}
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value
        }
    }


@router.post("/refresh")
@limiter.limit("20/minute")
def refresh_token(request: Request, refresh_token: str, db: Session = Depends(get_db)):
    """Refresh access token"""
    from app.core.security import decode_token
    
    try:
        payload = decode_token(refresh_token)
        
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == int(user_id)).first()
        
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        # Create new access token
        new_access_token = create_access_token(
            data={"sub": str(user.id), "role": user.role.value}
        )
        
        return {
            "access_token": new_access_token,
            "token_type": "bearer"
        }
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
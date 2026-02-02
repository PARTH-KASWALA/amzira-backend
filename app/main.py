from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import time

from app.core.config import settings
from app.api.v1 import auth, products, cart, orders, users, payments, admin, reviews, wishlist, coupons

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc"
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Trusted hosts (for production)
if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["amzira.com", "www.amzira.com", "api.amzira.com"]
    )

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])
app.include_router(products.router, prefix=f"{settings.API_V1_STR}/products", tags=["Products"])
app.include_router(cart.router, prefix=f"{settings.API_V1_STR}/cart", tags=["Cart"])
app.include_router(orders.router, prefix=f"{settings.API_V1_STR}/orders", tags=["Orders"])
app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
app.include_router(payments.router, prefix=f"{settings.API_V1_STR}/payments", tags=["Payments"])
app.include_router(admin.router, prefix=f"{settings.API_V1_STR}/admin", tags=["Admin"])
app.include_router(reviews.router, prefix=f"{settings.API_V1_STR}/reviews", tags=["Reviews"])
app.include_router(wishlist.router, prefix=f"{settings.API_V1_STR}/wishlist", tags=["Wishlist"])
app.include_router(coupons.router, prefix=f"{settings.API_V1_STR}/coupons", tags=["Coupons"])

# Health check
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0"
    }

# Root endpoint
@app.get("/")
def root():
    return {
        "message": "AMZIRA E-Commerce API",
        "docs": f"{settings.API_V1_STR}/docs",
        "version": "1.0.0"
    }

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if settings.DEBUG:
        # In development, show full error
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": str(exc),
                "type": type(exc).__name__
            }
        )
    else:
        # In production, hide details
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"}
        )
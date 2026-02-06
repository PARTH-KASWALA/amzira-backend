# from fastapi import FastAPI, Request, status
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.middleware.trustedhost import TrustedHostMiddleware
# from fastapi.responses import JSONResponse
# from fastapi.staticfiles import StaticFiles
# from slowapi import Limiter, _rate_limit_exceeded_handler
# from slowapi.util import get_remote_address
# from slowapi.errors import RateLimitExceeded
# import time
# from app.core.logging import configure_logging


# from app.core.config import settings
# from app.api.v1 import auth, products, cart, orders, users, payments, admin, reviews, wishlist, coupons

# # Create FastAPI app
# app = FastAPI(
#     title=settings.PROJECT_NAME,
#     openapi_url=f"{settings.API_V1_STR}/openapi.json",
#     docs_url=f"{settings.API_V1_STR}/docs",
#     redoc_url=f"{settings.API_V1_STR}/redoc"
# )

# # Rate limiting
# limiter = Limiter(key_func=get_remote_address)
# app.state.limiter = limiter
# app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# # CORS middleware
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=settings.BACKEND_CORS_ORIGINS,
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
#     allow_headers=["*"],
# )

# # Trusted hosts (for production)
# if settings.ENVIRONMENT == "production":
#     app.add_middleware(
#         TrustedHostMiddleware,
#         allowed_hosts=["amzira.com", "www.amzira.com", "api.amzira.com"]
#     )

# # Security headers middleware
# @app.middleware("http")
# async def add_security_headers(request: Request, call_next):
#     response = await call_next(request)
#     response.headers["X-Content-Type-Options"] = "nosniff"
#     response.headers["X-Frame-Options"] = "DENY"
#     response.headers["X-XSS-Protection"] = "1; mode=block"
#     if settings.ENVIRONMENT == "production":
#         response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
#     return response

# # Request timing middleware
# @app.middleware("http")
# async def add_process_time_header(request: Request, call_next):
#     start_time = time.time()
#     response = await call_next(request)
#     process_time = time.time() - start_time
#     response.headers["X-Process-Time"] = str(process_time)
#     return response

# # Mount static files
# app.mount("/static", StaticFiles(directory="static"), name="static")

# # Include routers
# app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])
# app.include_router(products.router, prefix=f"{settings.API_V1_STR}/products", tags=["Products"])
# app.include_router(cart.router, prefix=f"{settings.API_V1_STR}/cart", tags=["Cart"])
# app.include_router(orders.router, prefix=f"{settings.API_V1_STR}/orders", tags=["Orders"])
# app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
# app.include_router(payments.router, prefix=f"{settings.API_V1_STR}/payments", tags=["Payments"])
# app.include_router(admin.router, prefix=f"{settings.API_V1_STR}/admin", tags=["Admin"])
# app.include_router(reviews.router, prefix=f"{settings.API_V1_STR}/reviews", tags=["Reviews"])
# app.include_router(wishlist.router, prefix=f"{settings.API_V1_STR}/wishlist", tags=["Wishlist"])
# app.include_router(coupons.router, prefix=f"{settings.API_V1_STR}/coupons", tags=["Coupons"])

# # Health check
# @app.get("/health")
# def health_check():
#     return {
#         "status": "healthy",
#         "environment": settings.ENVIRONMENT,
#         "version": "1.0.0"
#     }

# # Root endpoint
# @app.get("/")
# def root():
#     return {
#         "message": "AMZIRA E-Commerce API",
#         "docs": f"{settings.API_V1_STR}/docs",
#         "version": "1.0.0"
#     }

# # Global exception handler
# @app.exception_handler(Exception)
# async def global_exception_handler(request: Request, exc: Exception):
#     if settings.DEBUG:
#         # In development, show full error
#         return JSONResponse(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             content={
#                 "detail": str(exc),
#                 "type": type(exc).__name__
#             }
#         )
#     else:
#         # In production, hide details
#         return JSONResponse(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             content={"detail": "Internal server error"}
#         )
    

# import sentry_sdk
# from sentry_sdk.integrations.fastapi import FastApiIntegration
# from app.core.config import settings

# sentry_sdk.init(
#     dsn=settings.SENTRY_DSN,
#     integrations=[FastApiIntegration()],
#     traces_sample_rate=0.2,
# )









# from sentry_sdk.integrations.fastapi import FastApiIntegration
# from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
# from slowapi.middleware import SlowAPIMiddleware


# # --------------------------------------------------
# # CONFIGURE LOGGING (FIRST)
# # --------------------------------------------------
# configure_logging()

# # --------------------------------------------------
# # INITIALIZE SENTRY (ONLY IN PRODUCTION)
# # --------------------------------------------------
# if settings.ENVIRONMENT == "production" and settings.SENTRY_DSN:
#     sentry_sdk.init(
#         dsn=settings.SENTRY_DSN,
#         environment=settings.ENVIRONMENT,
#         traces_sample_rate=0.1,
#         integrations=[
#             FastApiIntegration(),
#             SqlalchemyIntegration(),
#         ],
#     )

# # --------------------------------------------------
# # CREATE APP
# # --------------------------------------------------
# app = FastAPI(title="AMZIRA API")

# # --------------------------------------------------
# # RATE LIMITER SETUP (GLOBAL)
# # --------------------------------------------------
# limiter = Limiter(key_func=get_remote_address)
# app.state.limiter = limiter

# app.add_middleware(SlowAPIMiddleware)

# @app.exception_handler(RateLimitExceeded)
# async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
#     return JSONResponse(
#         status_code=429,
#         content={"detail": "Too many requests. Please try again later."},
#     )

# # --------------------------------------------------
# # REQUEST LOGGING MIDDLEWARE
# # --------------------------------------------------
# @app.middleware("http")
# async def log_requests(request: Request, call_next):
#     import structlog

#     logger = structlog.get_logger()

#     logger.info(
#         "request_started",
#         method=request.method,
#         path=request.url.path,
#         client_ip=request.client.host if request.client else None,
#     )

#     response = await call_next(request)

#     logger.info(
#         "request_completed",
#         method=request.method,
#         path=request.url.path,
#         status_code=response.status_code,
#     )

#     return response









import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import time
import structlog
import uuid

from app.core.logging import configure_logging
from app.core.config import settings
from app.core.celery_app import celery_app
from app.api.v1 import auth, products, cart, orders, users, payments, admin, reviews, wishlist, coupons

# --------------------------------------------------
# CONFIGURE LOGGING (FIRST)
# --------------------------------------------------
configure_logging()

# --------------------------------------------------
# INITIALIZE SENTRY (ONLY IN PRODUCTION)
# --------------------------------------------------
if settings.ENVIRONMENT == "production" and settings.SENTRY_DSN:
    try:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.1,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
        )
        import logging
        logging.info("Sentry initialized successfully")
    except Exception as e:
        import logging
        logging.warning(f"Failed to initialize Sentry: {e}")
        # Application continues without Sentry monitoring

# --------------------------------------------------
# CREATE FASTAPI APP (SINGLE INITIALIZATION)
# --------------------------------------------------
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc"
)

# --------------------------------------------------
# RATE LIMITING SETUP
# --------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )

# --------------------------------------------------
# CORS MIDDLEWARE
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Requested-With",
    ],
    expose_headers=["X-Process-Time"],
    max_age=3600,
)

# --------------------------------------------------
# TRUSTED HOSTS (PRODUCTION ONLY)
# --------------------------------------------------
if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["amzira.com", "www.amzira.com", "api.amzira.com"]
    )

# --------------------------------------------------
# SECURITY HEADERS MIDDLEWARE
# --------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def add_csp_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com; "
        "style-src 'self' 'unsafe-inline';"
    )
    return response

# --------------------------------------------------
# REQUEST TIMING MIDDLEWARE
# --------------------------------------------------
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

    request.state.correlation_id = correlation_id
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")

    response.headers["X-Correlation-ID"] = correlation_id
    return response

# --------------------------------------------------
# REQUEST LOGGING MIDDLEWARE
# --------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger = structlog.get_logger()

    logger.info(
        "request_started",
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else None,
    )

    response = await call_next(request)

    logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    )

    return response

# --------------------------------------------------
# MOUNT STATIC FILES
# --------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------------------------------------
# INCLUDE ROUTERS
# --------------------------------------------------
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

# --------------------------------------------------
# HEALTH CHECK ENDPOINT
# --------------------------------------------------
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0"
    }


@app.get("/health/email")
def email_health_check():
    try:
        with celery_app.connection_or_acquire() as connection:
            connection.ensure_connection(max_retries=1)

        inspector = celery_app.control.inspect(timeout=1.0)
        active_workers = inspector.active()

        if not active_workers:
            return {"status": "unhealthy", "reason": "No active Celery workers"}

        return {"status": "healthy", "workers": len(active_workers)}
    except Exception as exc:
        return {"status": "unhealthy", "reason": str(exc)}

# --------------------------------------------------
# ROOT ENDPOINT
# --------------------------------------------------
@app.get("/")
def root():
    return {
        "message": "AMZIRA E-Commerce API",
        "docs": f"{settings.API_V1_STR}/docs",
        "version": "1.0.0"
    }

# --------------------------------------------------
# GLOBAL EXCEPTION HANDLER
# --------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Let HTTPException instances propagate to FastAPI's default handler
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        raise exc

    # Unexpected exceptions: log and return controlled response
    import structlog
    logger = structlog.get_logger()
    logger.exception("unhandled_exception", error_type=type(exc).__name__, detail=str(exc))

    if settings.DEBUG:
        # In development, show full error
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": str(exc),
                "type": type(exc).__name__,
            },
        )

    # In production, hide details
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
    



# app/main.py
from app.middleware.csrf import verify_csrf_token

CSRF_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
}
CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if request.method in CSRF_PROTECTED_METHODS and path not in CSRF_EXEMPT_PATHS:
        if not verify_csrf_token(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF validation failed"}
            )
    
    response = await call_next(request)
    return response

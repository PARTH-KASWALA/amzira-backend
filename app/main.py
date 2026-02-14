import sentry_sdk
import os
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from datetime import datetime
from fastapi import FastAPI, Request, status, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import time
import structlog
import uuid
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.core.config import settings
from app.core.celery_app import celery_app
from app.core.exceptions import APIError
from app.core.rate_limiter import limiter
from app.db.session import SessionLocal, engine, get_db
from app.models.category import Category
from app.models.product import Product, Occasion, product_occasions
from app.models.user import User, UserRole
from app.api.v1 import auth, products, cart, orders, users, payments, admin, reviews, wishlist, coupons, returns, stock, categories

API_VERSION = "1.0.0"
SOFT_LAUNCH_REQUIREMENTS = [
    {"category_slug": "men", "occasion_slug": "wedding", "min_products": 1},
    {"category_slug": "men", "occasion_slug": "reception", "min_products": 1},
    {"category_slug": "men", "occasion_slug": "engagement", "min_products": 1},
    {"category_slug": "women", "occasion_slug": "wedding", "min_products": 1},
    {"category_slug": "women", "occasion_slug": "reception", "min_products": 1},
    {"category_slug": "kids", "occasion_slug": "festive", "min_products": 1},
]


def standardized_error_response(status_code: int, message: str, errors=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(
            {
                "success": False,
                "message": message,
                "data": None,
                "errors": errors or [],
                "timestamp": f"{datetime.utcnow().isoformat()}Z",
            }
        ),
    )

# --------------------------------------------------
# CONFIGURE LOGGING (FIRST)
# --------------------------------------------------
configure_logging()
# TODO: Integrate APM instrumentation (e.g., OpenTelemetry) for traces/metrics.
# TODO: Ship structured logs to centralized log aggregation in production.
# TODO: Configure alerting for elevated API error rates (5xx/4xx spikes).
# TODO: Configure alerting for repeated payment_failed events.

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


@app.on_event("startup")
def validate_production_admin_bootstrap():
    """Fail fast in production when no admin account exists."""
    if settings.ENVIRONMENT != "production":
        return

    db = SessionLocal()
    try:
        admin_exists = (
            db.query(User.id)
            .filter(User.role == UserRole.ADMIN, User.is_active == True)
            .first()
            is not None
        )
    finally:
        db.close()

    if not admin_exists:
        raise RuntimeError(
            "No active admin user found in production. "
            "Create an admin user before starting the API."
        )

# --------------------------------------------------
# RATE LIMITING SETUP
# --------------------------------------------------
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return standardized_error_response(
        status_code=429,
        message="Too many requests. Please try again later.",
    )

# --------------------------------------------------
# CORS MIDDLEWARE
# --------------------------------------------------
cors_origins = list(settings.BACKEND_CORS_ORIGINS)
# Always include the configured frontend origin (exact match required for cookies).
if settings.FRONTEND_URL and settings.FRONTEND_URL not in cors_origins:
    cors_origins.append(settings.FRONTEND_URL)
# Temporary dev mode CORS; restrict in production
if settings.ENVIRONMENT != "production":
    for origin in [
        "null",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
    ]:
        if origin not in cors_origins:
            cors_origins.append(origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
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
app.include_router(categories.router, prefix=f"{settings.API_V1_STR}/categories", tags=["Categories"])
app.include_router(products.router, prefix=f"{settings.API_V1_STR}/products", tags=["Products"])
app.include_router(cart.router, prefix=f"{settings.API_V1_STR}/cart", tags=["Cart"])
app.include_router(orders.router, prefix=f"{settings.API_V1_STR}/orders", tags=["Orders"])
app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
app.include_router(payments.router, prefix=f"{settings.API_V1_STR}/payments", tags=["Payments"])
app.include_router(admin.router, prefix=f"{settings.API_V1_STR}/admin", tags=["Admin"])
app.include_router(reviews.router, prefix=f"{settings.API_V1_STR}/reviews", tags=["Reviews"])
app.include_router(wishlist.router, prefix=f"{settings.API_V1_STR}/wishlist", tags=["Wishlist"])
app.include_router(coupons.router, prefix=f"{settings.API_V1_STR}/coupons", tags=["Coupons"])
app.include_router(returns.router, prefix=f"{settings.API_V1_STR}/returns", tags=["Returns"])
app.include_router(stock.router, prefix=f"{settings.API_V1_STR}/stock", tags=["Stock"])

# --------------------------------------------------
# HEALTH CHECK ENDPOINT
# --------------------------------------------------
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": API_VERSION
    }


@app.get("/health/email")
def email_health_check():
    try:
        with celery_app.connection_or_acquire() as connection:
            connection.ensure_connection(max_retries=1)
    except Exception as exc:
        return {"status": "unhealthy", "reason": f"Redis connection failed: {exc}"}

    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        if not inspector:
            return {"status": "unhealthy", "reason": "Celery inspect unavailable"}

        active_workers = inspector.ping() or {}
        if not active_workers:
            return {"status": "unhealthy", "reason": "No active Celery workers"}

        return {"status": "healthy", "workers": len(active_workers)}
    except Exception as exc:
        return {"status": "unhealthy", "reason": f"Celery worker check failed: {exc}"}


@app.get("/health/email/queue")
def email_queue_stats():
    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        if not inspector:
            return {"status": "unhealthy", "reason": "Celery inspect unavailable"}

        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
        scheduled = inspector.scheduled() or {}

        workers = sorted(set(active.keys()) | set(reserved.keys()) | set(scheduled.keys()))
        stats = {
            worker: {
                "active": len(active.get(worker, [])),
                "reserved": len(reserved.get(worker, [])),
                "scheduled": len(scheduled.get(worker, [])),
            }
            for worker in workers
        }
        return {"status": "healthy", "workers": stats}
    except Exception as exc:
        return {"status": "unhealthy", "reason": f"Queue stats unavailable: {exc}"}


@app.get("/health/email-queue")
def email_queue_stats_alias():
    """Backward-compatible alias for email queue health checks."""
    return email_queue_stats()


@app.get("/health/database")
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

        pool = engine.pool
        metrics = {
            "pool_class": pool.__class__.__name__,
            "size": pool.size() if hasattr(pool, "size") else None,
            "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else None,
            "overflow": pool.overflow() if hasattr(pool, "overflow") else None,
            "timeout": pool.timeout() if hasattr(pool, "timeout") else None,
            "status": pool.status() if hasattr(pool, "status") else None,
        }
        return {"status": "healthy", "pool": metrics}
    except Exception as exc:
        return {
            "status": "unhealthy",
            "pool": {},
            "reason": f"Database connectivity check failed: {exc}",
        }


@app.get("/health/catalog-launch")
def catalog_launch_health_check(db: Session = Depends(get_db)):
    """Validate soft-launch catalog coverage for MEN/WOMEN/KIDS occasions."""
    try:
        counts = {
            (category_slug, occasion_slug): product_count
            for category_slug, occasion_slug, product_count in (
                db.query(
                    Category.slug.label("category_slug"),
                    Occasion.slug.label("occasion_slug"),
                    func.count(Product.id).label("product_count"),
                )
                .join(Product, Product.category_id == Category.id)
                .join(product_occasions, product_occasions.c.product_id == Product.id)
                .join(Occasion, Occasion.id == product_occasions.c.occasion_id)
                .filter(
                    Product.is_active == True,
                    Category.is_active == True,
                )
                .group_by(Category.slug, Occasion.slug)
                .all()
            )
        }

        missing = []
        coverage = []
        for req in SOFT_LAUNCH_REQUIREMENTS:
            key = (req["category_slug"], req["occasion_slug"])
            found = int(counts.get(key, 0))
            entry = {
                "category": req["category_slug"],
                "occasion": req["occasion_slug"],
                "required_minimum": req["min_products"],
                "active_products": found,
                "ready": found >= req["min_products"],
            }
            coverage.append(entry)
            if found < req["min_products"]:
                missing.append(entry)

        return {
            "status": "healthy" if not missing else "unhealthy",
            "requirements_total": len(SOFT_LAUNCH_REQUIREMENTS),
            "requirements_ready": len(SOFT_LAUNCH_REQUIREMENTS) - len(missing),
            "requirements_missing": len(missing),
            "coverage": coverage,
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "reason": f"Catalog launch validation failed: {exc}",
        }

# --------------------------------------------------
# ROOT ENDPOINT
# --------------------------------------------------
@app.get("/")
def root():
    return {
        "message": "AMZIRA E-Commerce API",
        "docs": f"{settings.API_V1_STR}/docs",
        "version": API_VERSION
    }


@app.get(f"{settings.API_V1_STR}/version")
def get_version():
    return {
        "version": API_VERSION,
        "commit": os.getenv("GIT_COMMIT", "unknown"),
    }


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return standardized_error_response(
        status_code=exc.status_code,
        message=exc.message,
        errors=exc.errors,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail

    if isinstance(detail, str):
        message = detail
        errors = []
    elif isinstance(detail, list):
        message = "Request failed"
        errors = detail
    elif isinstance(detail, dict):
        message = detail.get("message", "Request failed")
        errors = detail.get("errors", [])
    else:
        message = "Request failed"
        errors = []

    return standardized_error_response(
        status_code=exc.status_code,
        message=message,
        errors=errors,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return standardized_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Validation failed",
        errors=exc.errors(),
    )

# --------------------------------------------------
# GLOBAL EXCEPTION HANDLER
# --------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Unexpected exceptions: log and return controlled response
    import structlog
    logger = structlog.get_logger()
    logger.exception("unhandled_exception", error_type=type(exc).__name__, detail=str(exc))

    if settings.DEBUG and settings.ENVIRONMENT != "production":
        return standardized_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Internal server error: {str(exc)}",
            errors=[{"type": type(exc).__name__}],
        )

    return standardized_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="Internal server error",
    )
    
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
    if settings.ENVIRONMENT != "production" and request.url.path.startswith("/api/"):
        return await call_next(request)
    path = request.url.path.rstrip("/") or "/"
    if request.method in CSRF_PROTECTED_METHODS and path not in CSRF_EXEMPT_PATHS:
        if not verify_csrf_token(request):
            return standardized_error_response(
                status_code=403,
                message="CSRF validation failed",
            )
    
    response = await call_next(request)
    return response

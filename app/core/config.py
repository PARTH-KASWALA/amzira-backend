from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, model_validator
from typing import List, Optional
from pathlib import Path
import json
import ipaddress
from dotenv import load_dotenv


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    # Project Info
    PROJECT_NAME: str = "AMZIRA E-Commerce API"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Razorpay
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://amzira.com",
        "https://www.amzira.com"
    ]

    # Commerce pricing
    GST_RATE: float = 0.18
    FREE_SHIPPING_THRESHOLD: float = 2000.0
    DEFAULT_SHIPPING_CHARGE: float = 100.0
    
    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAILS_FROM_EMAIL: str = ""
    EMAILS_FROM_NAME: str = "AMZIRA"
    EMAILS_FROM_ORDERS: str = ""
    EMAILS_FROM_SHIPPING: str = ""
    EMAILS_FROM_SUPPORT: str = ""
    
    # File Upload
    MAX_UPLOAD_SIZE: int = 10485760  # 10MB
    ALLOWED_EXTENSIONS: List[str] = ["jpg", "jpeg", "png", "webp"]
    UPLOAD_DIR: str = "static/uploads/products"
    
    # Environment
    ENVIRONMENT: str = "development"
    ENV: Optional[str] = Field(default=None)

    DEBUG: bool = False
    
    # Frontend
    FRONTEND_URL: str = "https://amzira.com"
    
    # Monitoring (Optional - Add to .env for production)
    SENTRY_DSN: str = ""  # Optional: Sentry error tracking DSN

    # Shiprocket
    SHIPROCKET_EMAIL: str = ""
    SHIPROCKET_PASSWORD: str = ""
    SHIPROCKET_BASE_URL: str = "https://apiv2.shiprocket.in/v1/external"
    SHIPROCKET_CHANNEL_ID: str = ""
    SHIPROCKET_PICKUP_LOCATION: str = "Primary"
    SHIPROCKET_DEFAULT_WEIGHT_KG: float = 0.5
    SHIPROCKET_DEFAULT_LENGTH_CM: int = 10
    SHIPROCKET_DEFAULT_BREADTH_CM: int = 10
    SHIPROCKET_DEFAULT_HEIGHT_CM: int = 5
    SHIPROCKET_TOKEN_TTL_SECONDS: int = 864000
    SHIPROCKET_MAX_RETRIES: int = 3


    # Celery & Redis (Task Queue)
    REDIS_URL: str = "redis://localhost:6379/0"  # Default Redis URL
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    
    # Admin Security
    ADMIN_ALLOWED_IPS: str = ""  # Must be set via env in production
    DEFAULT_ADMIN_PASSWORD: str = ""
    TRUST_PROXY_HEADERS: bool = True
    TRUSTED_PROXY_IPS: str = "127.0.0.1,::1"

    @field_validator("ENVIRONMENT")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return value.lower().strip()

    @classmethod
    def _parse_admin_ip_list(cls, value) -> List[str]:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError as exc:
                    raise ValueError("ADMIN_ALLOWED_IPS must be valid JSON or comma-separated IPs") from exc
            return [ip.strip() for ip in raw.split(",")]
        if isinstance(value, list):
            return [str(ip).strip() for ip in value if str(ip).strip()]
        return value

    @field_validator("ADMIN_ALLOWED_IPS")
    @classmethod
    def validate_admin_ip_format(cls, value: str) -> str:
        normalized = cls._parse_admin_ip_list(value)
        for ip in normalized:
            try:
                ipaddress.ip_address(ip)
            except ValueError as exc:
                raise ValueError(f"Invalid IP address: {ip}") from exc
        return ",".join(normalized)

    @field_validator("TRUSTED_PROXY_IPS")
    @classmethod
    def validate_trusted_proxy_ip_format(cls, value: str) -> str:
        normalized = cls._parse_admin_ip_list(value)
        for ip in normalized:
            try:
                ipaddress.ip_address(ip)
            except ValueError as exc:
                raise ValueError(f"Invalid proxy IP address: {ip}") from exc
        return ",".join(normalized)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def validate_cors_origins(cls, value):
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError("BACKEND_CORS_ORIGINS must be valid JSON or comma-separated origins") from exc
                if isinstance(parsed, list):
                    return [str(origin).strip().rstrip("/") for origin in parsed if str(origin).strip()]
            return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
        if isinstance(value, list):
            return [str(origin).strip().rstrip("/") for origin in value if str(origin).strip()]
        return value

    @model_validator(mode="after")
    def validate_production_admin_ips(self):
        if self.ENVIRONMENT == "production" and not self.admin_allowed_ips:
            raise ValueError("ADMIN_ALLOWED_IPS must be set in production")
        if self.ENVIRONMENT == "production":
            normalized_secret = (self.SECRET_KEY or "").strip()
            if len(normalized_secret) < 32 or "your-secret-key-here" in normalized_secret.lower():
                raise ValueError("SECRET_KEY must be at least 32 chars and not use placeholders in production")
            if (self.RAZORPAY_KEY_ID or "").startswith("rzp_test_"):
                raise ValueError("RAZORPAY_KEY_ID must use live key in production")
            if any(origin == "*" for origin in self.BACKEND_CORS_ORIGINS):
                raise ValueError("BACKEND_CORS_ORIGINS cannot contain '*' in production")
        return self

    @property
    def admin_allowed_ips(self) -> List[str]:
        return self._parse_admin_ip_list(self.ADMIN_ALLOWED_IPS)

    @property
    def trusted_proxy_ips(self) -> List[str]:
        return self._parse_admin_ip_list(self.TRUSTED_PROXY_IPS)

    def is_trusted_proxy(self, ip: str | None) -> bool:
        if not ip:
            return False
        if ip in {"127.0.0.1", "::1"}:
            return True
        return ip in self.trusted_proxy_ips

    model_config = {
        "env_file": str(ENV_FILE),
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()

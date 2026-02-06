# from pydantic_settings import BaseSettings
# from typing import List
# import os


# class Settings(BaseSettings):
#     # Project Info
#     PROJECT_NAME: str = "AMZIRA E-Commerce API"
#     API_V1_STR: str = "/api/v1"
    
#     # Database
#     DATABASE_URL: str
    
#     # Security
#     SECRET_KEY: str
#     ALGORITHM: str = "HS256"
#     ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
#     REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
#     # Razorpay
#     RAZORPAY_KEY_ID: str
#     RAZORPAY_KEY_SECRET: str
#     RAZORPAY_WEBHOOK_SECRET: str
    
#     # CORS
#     BACKEND_CORS_ORIGINS: List[str] = [
#         "http://localhost:3000",
#         "https://amzira.com",
#         "https://www.amzira.com"
#     ]
    
#     # Email
#     SMTP_HOST: str = "smtp.gmail.com"
#     SMTP_PORT: int = 587
#     SMTP_USER: str = ""
#     SMTP_PASSWORD: str = ""
#     EMAILS_FROM_EMAIL: str = ""
#     EMAILS_FROM_NAME: str = "AMZIRA"
    
#     # File Upload
#     MAX_UPLOAD_SIZE: int = 10485760  # 10MB
#     ALLOWED_EXTENSIONS: List[str] = ["jpg", "jpeg", "png", "webp"]
#     UPLOAD_DIR: str = "static/uploads/products"
    
#     # Environment
#     ENVIRONMENT: str = "development"
#     DEBUG: bool = True
    
#     # Frontend
#     FRONTEND_URL: str = "https://amzira.com"
    
#     class Config:
#         env_file = ".env"
#         case_sensitive = True


# settings = Settings()



# # app/core/config.py
# ADMIN_ALLOWED_IPS: List[str] = ["103.x.x.x"]  # Office IP


from pydantic_settings import BaseSettings
from typing import List
from pydantic import field_validator, model_validator
import ipaddress


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
        "https://amzira.com",
        "https://www.amzira.com"
    ]
    
    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAILS_FROM_EMAIL: str = ""
    EMAILS_FROM_NAME: str = "AMZIRA"
    
    # File Upload
    MAX_UPLOAD_SIZE: int = 10485760  # 10MB
    ALLOWED_EXTENSIONS: List[str] = ["jpg", "jpeg", "png", "webp"]
    UPLOAD_DIR: str = "static/uploads/products"
    
    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    # Frontend
    FRONTEND_URL: str = "https://amzira.com"
    
    # Monitoring (Optional - Add to .env for production)
    SENTRY_DSN: str = ""  # Optional: Sentry error tracking DSN


    # Celery & Redis (Task Queue)
    REDIS_URL: str = "redis://localhost:6379/0"  # Default Redis URL
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    
    # Admin Security
    ADMIN_ALLOWED_IPS: List[str] = []  # Must be set via env in production

    @field_validator("ADMIN_ALLOWED_IPS")
    @classmethod
    def validate_admin_ip_format(cls, value: List[str]) -> List[str]:
        for ip in value:
            try:
                ipaddress.ip_address(ip)
            except ValueError as exc:
                raise ValueError(f"Invalid IP address: {ip}") from exc
        return value

    @model_validator(mode="after")
    def validate_production_admin_ips(self):
        if self.ENVIRONMENT == "production" and not self.ADMIN_ALLOWED_IPS:
            raise ValueError("ADMIN_ALLOWED_IPS must be set in production")
        return self
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

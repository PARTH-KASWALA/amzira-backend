from pydantic_settings import BaseSettings
from typing import List
import os


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
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
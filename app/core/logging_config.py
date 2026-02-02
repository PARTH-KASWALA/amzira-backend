import logging
import structlog
from app.core.config import settings

def configure_logging():
    """Configure structured logging"""
    
    # Console renderer for development
    renderer = structlog.dev.ConsoleRenderer() if settings.DEBUG else structlog.processors.JSONRenderer()
    
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Standard logging config
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    )
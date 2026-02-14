from sqlalchemy.orm import Session
import logging
from app.models.user import User, UserRole
from app.models.category import Category
from app.models.product import Occasion
from app.core.config import settings
from app.core.security import hash_password

logger = logging.getLogger(__name__)


def init_db(db: Session) -> None:
    """Initialize database with default data"""
    
    # Create admin user
    admin = db.query(User).filter(User.email == "admin@amzira.com").first()
    if not admin:
        seed_password = (settings.DEFAULT_ADMIN_PASSWORD or "").strip()
        if not seed_password:
            message = (
                "Missing admin bootstrap credentials: set DEFAULT_ADMIN_PASSWORD "
                "or create an admin user manually before launch."
            )
            if settings.ENVIRONMENT == "production":
                logger.error("%s env=%s", message, settings.ENVIRONMENT)
                raise RuntimeError(message)
            logger.warning("%s env=%s", message, settings.ENVIRONMENT)
        else:
            admin = User(
                email="admin@amzira.com",
                password_hash=hash_password(seed_password),
                full_name="AMZIRA Admin",
                role=UserRole.ADMIN,
                is_active=True,
                is_verified=True
            )
            db.add(admin)
            logger.info("admin_user_created email=admin@amzira.com")
    
    # Create categories
    categories_data = [
        {"name": "Sherwani", "description": "Traditional men's wedding attire"},
        {"name": "Kurta Jacket Sets", "description": "Elegant Indo-Western outfits"},
        {"name": "Lehenga Choli", "description": "Traditional women's ethnic wear"},
        {"name": "Ethnic Wear", "description": "Complete ethnic collection"}
    ]
    
    for cat_data in categories_data:
        existing = db.query(Category).filter(Category.name == cat_data["name"]).first()
        if not existing:
            from slugify import slugify
            category = Category(
                name=cat_data["name"],
                slug=slugify(cat_data["name"]),
                description=cat_data["description"]
            )
            db.add(category)
            logger.info("category_created name=%s", cat_data["name"])
    
    # Create occasions
    occasions_data = ["Wedding", "Reception", "Sangeet", "Engagement", "Festival", "Party"]
    
    for occ_name in occasions_data:
        existing = db.query(Occasion).filter(Occasion.name == occ_name).first()
        if not existing:
            from slugify import slugify
            occasion = Occasion(
                name=occ_name,
                slug=slugify(occ_name)
            )
            db.add(occasion)
            logger.info("occasion_created name=%s", occ_name)
    
    db.commit()
    logger.info("database_initialized")


if __name__ == "__main__":
    from app.db.session import SessionLocal
    db = SessionLocal()
    init_db(db)
    db.close()

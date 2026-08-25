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
    
    # Create or migrate the admin user
    configured_email = (settings.ADMIN_EMAIL or "").strip().lower()
    if not configured_email:
        raise RuntimeError("ADMIN_EMAIL must be set before initializing the database")

    legacy_email = "admin@amzira.com"
    admin = db.query(User).filter(User.email == configured_email).first()
    if not admin and configured_email != legacy_email:
        legacy_admin = (
            db.query(User)
            .filter(User.email == legacy_email, User.role == UserRole.ADMIN)
            .first()
        )
        if legacy_admin:
            legacy_admin.email = configured_email
            db.add(legacy_admin)
            admin = legacy_admin
            logger.info("admin_email_migrated from=%s to=%s", legacy_email, configured_email)

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
                email=configured_email,
                password_hash=hash_password(seed_password),
                full_name="AMZIRA Admin",
                role=UserRole.ADMIN,
                is_active=True,
                is_verified=True
            )
            db.add(admin)
            logger.info("admin_user_created email=%s", configured_email)
    
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

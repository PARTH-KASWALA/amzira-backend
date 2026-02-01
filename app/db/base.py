from app.db.base_class import Base


# IMPORT ALL MODELS HERE (THIS REGISTERS THEM WITH Base.metadata)
from app.models.user import User
from app.models.address import Address
from app.models.category import Category
# from app.models.subcategory import SubCategory
from app.models.product import Product
# from app.models.product_image import ProductImage
# from app.models.product_variant import ProductVariant
# from app.models.occasion import Occasion
from app.models.order import Order
# from app.models.order_item import OrderItem
# from app.models.cart_item import CartItem
from app.models.payment import Payment
# from app.models.product_occasion import ProductOccasion
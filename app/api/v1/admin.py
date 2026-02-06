from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from slugify import slugify
from app.db.session import get_db
from app.api.deps import require_admin
from app.models.user import User
from app.models.product import Product, ProductImage, ProductVariant, Occasion
from app.models.category import Category, Subcategory
from app.models.order import Order, OrderStatus
from app.schemas.product import ProductCreate
from app.utils.image_upload import save_product_image, delete_product_image
from app.utils.response import success

router = APIRouter()


# ============= PRODUCT MANAGEMENT =============

@router.post("/products", status_code=201)
def create_product(
    name: str = Form(...),
    category_id: int = Form(...),
    description: Optional[str] = Form(None),
    base_price: float = Form(...),
    sale_price: Optional[float] = Form(None),
    fabric: Optional[str] = Form(None),
    care_instructions: Optional[str] = Form(None),
    is_featured: bool = Form(False),
    subcategory_id: Optional[int] = Form(None),
    occasion_ids: str = Form(""),  # Comma-separated IDs
    images: List[UploadFile] = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create new product"""
    # Generate slug
    slug = slugify(name)
    
    # Check if slug exists
    existing = db.query(Product).filter(Product.slug == slug).first()
    if existing:
        slug = f"{slug}-{db.query(Product).count() + 1}"
    
    # Calculate discount
    discount = 0
    if sale_price and sale_price < base_price:
        discount = int(((base_price - sale_price) / base_price) * 100)
    
    # Create product
    product = Product(
        name=name,
        slug=slug,
        category_id=category_id,
        subcategory_id=subcategory_id,
        description=description,
        base_price=base_price,
        sale_price=sale_price,
        discount_percentage=discount,
        fabric=fabric,
        care_instructions=care_instructions,
        is_featured=is_featured
    )
    
    db.add(product)
    db.flush()  # Get product ID
    
    # Add occasions
    if occasion_ids:
        occ_ids = [int(id.strip()) for id in occasion_ids.split(",") if id.strip()]
        occasions = db.query(Occasion).filter(Occasion.id.in_(occ_ids)).all()
        product.occasions = occasions
    
    # Upload images
    for idx, image_file in enumerate(images):
        image_url = save_product_image(image_file)
        
        product_image = ProductImage(
            product_id=product.id,
            image_url=image_url,
            alt_text=name,
            display_order=idx,
            is_primary=(idx == 0)
        )
        db.add(product_image)
    
    db.commit()
    db.refresh(product)
    
    return success(
        data={"product_id": product.id, "slug": product.slug},
        message="Product created successfully",
    )


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    base_price: Optional[float] = Form(None),
    sale_price: Optional[float] = Form(None),
    fabric: Optional[str] = Form(None),
    is_featured: Optional[bool] = Form(None),
    is_active: Optional[bool] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update product"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if name:
        product.name = name
        product.slug = slugify(name)
    
    if description is not None:
        product.description = description
    
    if base_price is not None:
        product.base_price = base_price
    
    if sale_price is not None:
        product.sale_price = sale_price
        # Recalculate discount
        if sale_price and sale_price < product.base_price:
            product.discount_percentage = int(((product.base_price - sale_price) / product.base_price) * 100)
    
    if fabric is not None:
        product.fabric = fabric
    
    if is_featured is not None:
        product.is_featured = is_featured
    
    if is_active is not None:
        product.is_active = is_active
    
    db.commit()
    
    return success(message="Product updated successfully")


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Delete product (soft delete)"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Soft delete (set inactive)
    product.is_active = False
    db.commit()
    
    return success(message="Product deleted successfully")


@router.post("/products/{product_id}/images")
async def add_product_images(
    product_id: int,
    images: List[UploadFile] = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Add more images to product"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Get current max display order
    max_order = db.query(ProductImage).filter(
        ProductImage.product_id == product_id
    ).count()
    
    for idx, image_file in enumerate(images):
        image_url = save_product_image(image_file)
        
        product_image = ProductImage(
            product_id=product_id,
            image_url=image_url,
            alt_text=product.name,
            display_order=max_order + idx
        )
        db.add(product_image)
    
    db.commit()
    
    return success(message=f"{len(images)} images added successfully")


@router.delete("/products/images/{image_id}")
def delete_product_image_endpoint(
    image_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Delete product image"""
    image = db.query(ProductImage).filter(ProductImage.id == image_id).first()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Delete file
    delete_product_image(image.image_url)
    
    # Delete from DB
    db.delete(image)
    db.commit()
    
    return success(message="Image deleted successfully")


# ============= VARIANT MANAGEMENT =============

@router.post("/products/{product_id}/variants")
def add_product_variant(
    product_id: int,
    size: str = Form(...),
    color: Optional[str] = Form(None),
    stock_quantity: int = Form(...),
    additional_price: float = Form(0.0),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Add product variant"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Generate SKU
    sku = f"{product.slug[:10].upper()}-{size}"
    if color:
        sku += f"-{color[:3].upper()}"
    sku += f"-{db.query(ProductVariant).count() + 1}"
    
    variant = ProductVariant(
        product_id=product_id,
        size=size,
        color=color,
        sku=sku,
        stock_quantity=stock_quantity,
        additional_price=additional_price
    )
    
    db.add(variant)
    
    # Update product total stock
    product.total_stock = sum(v.stock_quantity for v in product.variants) + stock_quantity
    
    db.commit()
    
    return success(data={"sku": sku}, message="Variant added successfully")


@router.put("/variants/{variant_id}")
def update_variant_stock(
    variant_id: int,
    stock_quantity: int = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update variant stock"""
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")
    
    variant.stock_quantity = stock_quantity
    
    # Update product total stock
    product = variant.product
    product.total_stock = sum(v.stock_quantity for v in product.variants)
    
    db.commit()
    
    return success(message="Stock updated successfully")


# ============= ORDER MANAGEMENT =============

@router.get("/orders")
def get_all_orders(
    status: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get all orders"""
    query = db.query(Order)
    
    if status:
        query = query.filter(Order.status == status)
    
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    
    orders_response = []
    for order in orders:
        orders_response.append({
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.user.full_name,
            "customer_email": order.user.email,
            "status": order.status.value,
            "total_amount": order.total_amount,
            "items_count": len(order.items),
            "created_at": order.created_at,
            "tracking_number": order.tracking_number
        })
    
    return success(
        data={
            "total": total,
            "page": page,
            "limit": limit,
            "orders": orders_response,
        },
        message="Orders retrieved successfully",
    )


@router.get("/orders/{order_id}")
def get_order_detail_admin(
    order_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get order details"""
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    items = [
        {
            "product_name": item.product_name,
            "variant_details": item.variant_details,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "total_price": item.total_price
        }
        for item in order.items
    ]
    
    return success(
        data={
            "id": order.id,
            "order_number": order.order_number,
            "customer": {
                "name": order.user.full_name,
                "email": order.user.email,
                "phone": order.user.phone,
            },
            "status": order.status.value,
            "subtotal": order.subtotal,
            "tax_amount": order.tax_amount,
            "shipping_charge": order.shipping_charge,
            "total_amount": order.total_amount,
            "items": items,
            "shipping_address": {
                "full_name": order.shipping_address.full_name,
                "phone": order.shipping_address.phone,
                "address_line1": order.shipping_address.address_line1,
                "address_line2": order.shipping_address.address_line2,
                "city": order.shipping_address.city,
                "state": order.shipping_address.state,
                "pincode": order.shipping_address.pincode,
            },
            "payment": {
                "method": order.payment.payment_method.value if order.payment else None,
                "status": order.payment.payment_status.value if order.payment else None,
            },
            "customer_notes": order.customer_notes,
            "admin_notes": order.admin_notes,
            "tracking_number": order.tracking_number,
            "created_at": order.created_at,
        },
        message="Order details retrieved successfully",
    )


@router.put("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    status: str = Form(...),
    tracking_number: Optional[str] = Form(None),
    admin_notes: Optional[str] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Update order status"""
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Validate status
    try:
        order.status = OrderStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    if tracking_number:
        order.tracking_number = tracking_number
    
    if admin_notes:
        order.admin_notes = admin_notes
    
    db.commit()
    
    # TODO: Send email notification to customer
    
    return success(message="Order status updated successfully")


# ============= CATEGORY MANAGEMENT =============

@router.post("/categories")
def create_category(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create category"""
    slug = slugify(name)
    
    # Check if exists
    existing = db.query(Category).filter(Category.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Category already exists")
    
    category = Category(
        name=name,
        slug=slug,
        description=description
    )
    
    db.add(category)
    db.commit()
    
    return success(data={"id": category.id}, message="Category created")


@router.post("/occasions")
def create_occasion(
    name: str = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Create occasion"""
    slug = slugify(name)
    
    # Check if exists
    existing = db.query(Occasion).filter(Occasion.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Occasion already exists")
    
    occasion = Occasion(name=name, slug=slug)
    db.add(occasion)
    db.commit()
    
    return success(data={"id": occasion.id}, message="Occasion created")


# ============= ANALYTICS =============

@router.get("/analytics")
def get_analytics(
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Get analytics dashboard"""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    # Total orders
    total_orders = db.query(Order).count()
    
    # Pending orders
    pending_orders = db.query(Order).filter(Order.status == OrderStatus.PENDING).count()
    
    # Total revenue
    total_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # Today's revenue
    today_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= today,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # This week's revenue
    week_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= week_ago,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # This month's revenue
    month_revenue = db.query(func.sum(Order.total_amount)).filter(
        Order.created_at >= month_ago,
        Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
    ).scalar() or 0
    
    # Top selling products
    from app.models.order import OrderItem
    top_products = db.query(
        OrderItem.product_name,
        func.sum(OrderItem.quantity).label('total_sold')
    ).group_by(OrderItem.product_name).order_by(func.sum(OrderItem.quantity).desc()).limit(5).all()
    
    return success(
        data={
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "total_revenue": total_revenue,
            "today_revenue": today_revenue,
            "week_revenue": week_revenue,
            "month_revenue": month_revenue,
            "top_products": [{"name": p[0], "sold": p[1]} for p in top_products],
        },
        message="Analytics retrieved successfully",
    )





# app/api/v1/admin.py

@router.post("/products/bulk-upload")
async def bulk_upload_products(
    file: UploadFile = File(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Bulk upload products from CSV"""
    import csv
    from io import StringIO
    
    content = await file.read()
    csv_data = StringIO(content.decode('utf-8'))
    reader = csv.DictReader(csv_data)
    
    created = []
    errors = []
    
    for row in reader:
        try:
            # Validate required fields
            required = ['name', 'category_id', 'base_price']
            missing = [f for f in required if not row.get(f)]
            if missing:
                errors.append(f"Row {reader.line_num}: Missing {missing}")
                continue
            
            # Create product
            slug = slugify(row['name'])
            product = Product(
                name=row['name'],
                slug=slug,
                category_id=int(row['category_id']),
                base_price=float(row['base_price']),
                sale_price=float(row['sale_price']) if row.get('sale_price') else None,
                description=row.get('description'),
                fabric=row.get('fabric'),
                is_featured=row.get('is_featured', '').lower() == 'true'
            )
            db.add(product)
            db.flush()
            
            # Add variants if provided
            if row.get('sizes'):
                sizes = row['sizes'].split(',')
                for size in sizes:
                    variant = ProductVariant(
                        product_id=product.id,
                        size=size.strip(),
                        sku=f"{slug}-{size.strip()}".upper(),
                        stock_quantity=int(row.get('stock', 0))
                    )
                    db.add(variant)
            
            created.append(product.name)
            
        except Exception as e:
            errors.append(f"Row {reader.line_num}: {str(e)}")
    
    db.commit()
    
    return success(
        data={
            "created_count": len(created),
            "error_count": len(errors),
            "created": created[:10],
            "errors": errors[:10],
        },
        message="Bulk upload completed",
    )


@router.get("/orders/export")
def export_orders(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin: Export orders to CSV"""
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    
    query = db.query(Order)
    
    if start_date:
        query = query.filter(Order.created_at >= start_date)
    if end_date:
        query = query.filter(Order.created_at <= end_date)
    if status:
        query = query.filter(Order.status == status)
    
    orders = query.all()
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'Order Number', 'Date', 'Customer', 'Email', 'Status',
        'Items', 'Total', 'Payment Method', 'Tracking'
    ])
    
    # Data
    for order in orders:
        writer.writerow([
            order.order_number,
            order.created_at.strftime('%Y-%m-%d %H:%M'),
            order.user.full_name,
            order.user.email,
            order.status.value,
            len(order.items),
            order.total_amount,
            order.payment.payment_method.value if order.payment else '',
            order.tracking_number or ''
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"}
    )

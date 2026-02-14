# API Contract Specification

Base URL: `/api/v1`

## Standard Response Envelope

### Success
```json
{
  "success": true,
  "message": "Operation successful",
  "data": {},
  "errors": null
}
```

### Error
```json
{
  "success": false,
  "message": "Human readable error message",
  "errors": [],
  "timestamp": "2026-02-06T10:30:00.000000Z"
}
```

## Authentication

### POST `/api/v1/auth/register`
Request:
```json
{
  "email": "user@example.com",
  "password": "StrongPass1",
  "full_name": "Jane Doe",
  "phone": "9876543210"
}
```
Response `201`:
```json
{
  "success": true,
  "message": "Registration successful",
  "data": {
    "id": 1,
    "email": "user@example.com"
  },
  "errors": null
}
```

### POST `/api/v1/auth/login`
Request:
```json
{
  "email": "user@example.com",
  "password": "StrongPass1"
}
```
Response `200`:
```json
{
  "success": true,
  "message": "Login successful",
  "data": {
    "user": {
      "id": 1,
      "email": "user@example.com",
      "role": "customer"
    }
  },
  "errors": null
}
```
Cookies set:
- `access_token` (`HttpOnly`, `SameSite=Lax`, 30 minutes)
- `refresh_token` (`HttpOnly`, `SameSite=Lax`, 7 days)

### POST `/api/v1/auth/refresh`
Request: No body (uses `refresh_token` cookie)

Response `200`:
```json
{
  "success": true,
  "message": "Token refreshed",
  "data": null,
  "errors": null
}
```

### POST `/api/v1/auth/logout`
Request: No body

Response `200`:
```json
{
  "success": true,
  "message": "Logout successful",
  "data": null,
  "errors": null
}
```

### GET `/api/v1/auth/csrf-token`
Response `200` sets CSRF cookie.

## Version and Health

### GET `/api/v1/version`
Response `200`:
```json
{
  "version": "1.0.0",
  "commit": "unknown"
}
```

### GET `/health`
### GET `/health/email`
### GET `/`
Operational endpoints outside `/api/v1`.

## Products

### GET `/api/v1/categories`
Response: active categories list ordered by `display_order` (primary) and `id` (secondary).

### GET `/api/v1/products/categories`
Response: active categories list.

### GET `/api/v1/products`
Query params:
- `page`, `limit`
- `category`, `subcategory`, `occasion`
- `min_price`, `max_price`, `search`, `featured`, `sort_by`

Response: paginated product list with normalized list item shape:
```json
{
  "success": true,
  "message": "Products retrieved",
  "data": {
    "total": 1,
    "page": 1,
    "limit": 20,
    "total_pages": 1,
    "products": [
      {
        "id": 1,
        "name": "Product Name",
        "slug": "product-name",
        "base_price": 1200,
        "sale_price": 999,
        "primary_image": "https://cdn.example.com/product.jpg",
        "stock_quantity": 7,
        "default_variant": {
          "variant_id": 10,
          "size": "M",
          "color": "Blue",
          "stock_quantity": 3
        },
        "category": {
          "id": 1,
          "name": "Men",
          "slug": "men"
        }
      }
    ]
  },
  "errors": null
}
```

`default_variant` rules:
- Includes only active, in-stock variants.
- Deterministically selects the lowest variant `id` among in-stock variants.
- Returns `null` when no variant is in stock.

### GET `/api/v1/products/{slug}`
Response: full product details with images, variants, occasions.

### GET `/api/v1/products/category/{category_slug}`
### GET `/api/v1/products/occasion/{occasion_slug}`
Response: filtered product list.

## Cart (Authenticated)

### GET `/api/v1/cart/`
Response: cart items, subtotal, total_items.

### POST `/api/v1/cart/items`
Request:
```json
{
  "product_id": 1,
  "variant_id": 2,
  "quantity": 1
}
```
Response `201`: `cart_item_id`.

Validation error `400`:
```json
{
  "success": false,
  "message": "variant_id is required and must be selected",
  "data": null,
  "errors": [],
  "timestamp": "2026-02-07T12:00:00Z"
}
```

### PUT `/api/v1/cart/items/{item_id}`
Request:
```json
{
  "quantity": 2
}
```

### DELETE `/api/v1/cart/items/{item_id}`
### DELETE `/api/v1/cart/`

## Orders (Authenticated unless stated)

### POST `/api/v1/orders/`
Request:
```json
{
  "shipping_address_id": 1,
  "billing_address_id": 1,
  "payment_method": "razorpay",
  "customer_notes": "Leave at door"
}
```
Response `201`: `order_id`, `order_number`, `status`.

### GET `/api/v1/orders/`
Response: current user order history.

### GET `/api/v1/orders/{order_number}`
Response: order details.

### PUT `/api/v1/orders/{order_id}/cancel`
Response: cancellation confirmation.

### PUT `/api/v1/orders/{order_id}/status` (Admin)
Request:
```json
{
  "status": "processing",
  "tracking_number": "TRACK123",
  "carrier_name": "Delhivery",
  "estimated_delivery_date": "2026-02-10T12:00:00Z",
  "notes": "Packed"
}
```

### GET `/api/v1/orders/{order_id}/tracking`
### GET `/api/v1/orders/my/tracking`

### GET `/api/v1/orders/orders/{order_number}/invoice`
Response: PDF stream (`application/pdf`).

## Payments (Authenticated unless webhook)

### POST `/api/v1/payments/create-order`
Request:
```json
{
  "order_id": 1
}
```
Response: `razorpay_order_id`, `razorpay_key_id`, `amount`, `currency`, `order_number`.

### POST `/api/v1/payments/verify`
Request:
```json
{
  "razorpay_order_id": "order_xxx",
  "razorpay_payment_id": "pay_xxx",
  "razorpay_signature": "signature"
}
```
Response: payment success + `order_number`.

### POST `/api/v1/payments/webhook`
Request: Razorpay webhook payload + `X-Razorpay-Signature` header.

## Stock

### POST `/api/v1/stock/check`
Canonical stock check endpoint.

Request:
```json
{
  "items": [
    { "variant_id": 101, "quantity": 2 },
    { "variant_id": 205, "quantity": 1 }
  ]
}
```

Response `200`:
```json
{
  "success": true,
  "message": "Success",
  "data": {
    "available": false,
    "items": [
      {
        "variant_id": 101,
        "available_quantity": 1,
        "requested_quantity": 2,
        "message": "Insufficient stock for variant 101"
      }
    ],
    "insufficient_items": [
      {
        "variant_id": 101,
        "available_quantity": 1,
        "requested_quantity": 2,
        "message": "Insufficient stock for variant 101"
      }
    ]
  },
  "errors": null
}
```

### GET `/api/v1/stock/check` (Legacy compatibility)
Supported temporarily with repeated query params:
- `variant_id`
- `quantity`

If query params are omitted and the user is authenticated, the endpoint uses cart items for stock validation.

Example:
`/api/v1/stock/check?variant_id=101&quantity=2&variant_id=205&quantity=1`

If payload is missing:
```json
{
  "success": false,
  "message": "items payload is required. Use POST /api/v1/stock/check",
  "data": null,
  "errors": [],
  "timestamp": "2026-02-07T12:00:00Z"
}
```

## Users (Authenticated)

### GET `/api/v1/users/me`
### PUT `/api/v1/users/me`
Request (update):
```json
{
  "full_name": "Updated Name",
  "phone": "9876543210"
}
```

### GET `/api/v1/users/me/addresses`
### POST `/api/v1/users/me/addresses`
Request:
```json
{
  "full_name": "Jane Doe",
  "phone": "9876543210",
  "address_line1": "Street 1",
  "address_line2": "Apt 10",
  "city": "Surat",
  "state": "Gujarat",
  "pincode": "395007",
  "country": "India",
  "address_type": "home",
  "is_default": true
}
```

### PUT `/api/v1/users/me/addresses/{address_id}`
### DELETE `/api/v1/users/me/addresses/{address_id}`

## Reviews

### POST `/api/v1/reviews/` (Authenticated)
Request:
```json
{
  "product_id": 1,
  "rating": 5,
  "comment": "Great quality"
}
```

### GET `/api/v1/reviews/product/{product_id}`
Query params: `page`, `per_page`

### PUT `/api/v1/reviews/{review_id}` (Authenticated)
### DELETE `/api/v1/reviews/{review_id}` (Authenticated)

## Wishlist (Authenticated)

### POST `/api/v1/wishlist/`
Request:
```json
{
  "product_id": 1
}
```

### DELETE `/api/v1/wishlist/{product_id}`
### GET `/api/v1/wishlist/`
### GET `/api/v1/wishlist/check/{product_id}`

## Coupons

### POST `/api/v1/coupons/` (Admin)
### GET `/api/v1/coupons/` (Admin)
### GET `/api/v1/coupons/{coupon_id}` (Admin)
### PUT `/api/v1/coupons/{coupon_id}` (Admin)

### POST `/api/v1/coupons/validate` (Authenticated)
Request:
```json
{
  "coupon_code": "WELCOME10",
  "order_total": 2500
}
```
Response includes `valid`, `discount_amount`, `final_total`, and message.

## Admin

All `/api/v1/admin/*` endpoints require admin authentication.

### Product management
- `POST /api/v1/admin/products` (multipart form + `images[]`)
- `PUT /api/v1/admin/products/{product_id}` (multipart form)
- `DELETE /api/v1/admin/products/{product_id}`
- `PUT /api/v1/admin/products/bulk-update-category` (JSON body)
- `POST /api/v1/admin/products/{product_id}/images` (multipart)
- `DELETE /api/v1/admin/products/images/{image_id}`
- `POST /api/v1/admin/products/{product_id}/variants` (multipart)
- `PUT /api/v1/admin/variants/{variant_id}` (multipart)
- `POST /api/v1/admin/products/bulk-upload` (CSV file upload)

### Order management
- `GET /api/v1/admin/orders`
- `GET /api/v1/admin/orders/{order_id}`
- `PUT /api/v1/admin/orders/{order_id}/status` (multipart form)
- `GET /api/v1/admin/orders/export`

### Taxonomy and analytics
- `POST /api/v1/admin/categories` (multipart form)
- `GET /api/v1/admin/categories`
- `PUT /api/v1/admin/categories/{category_id}` (multipart form)
- `DELETE /api/v1/admin/categories/{category_id}` (`hard_delete` query param optional)
- `POST /api/v1/admin/categories/bulk` (JSON body, atomic)
- `POST /api/v1/admin/occasions` (multipart form)
- `GET /api/v1/admin/analytics`

### Bulk workflow request examples

#### POST `/api/v1/admin/categories/bulk`
```json
{
  "categories": [
    { "name": "Men", "slug": "men", "display_order": 1 },
    { "name": "Women", "slug": "women", "display_order": 2 },
    { "name": "Kids", "slug": "kids", "display_order": 3 }
  ]
}
```

#### PUT `/api/v1/admin/products/bulk-update-category`
```json
{
  "product_ids": [12, 15, 18],
  "category_id": 3
}
```

### Error examples

#### `400` invalid category assignment
```json
{
  "success": false,
  "message": "Invalid category_id: 999",
  "errors": [],
  "timestamp": "2026-02-07T12:00:00Z"
}
```

#### `409` duplicate category slug in bulk request
```json
{
  "success": false,
  "message": "Duplicate slugs in request: men",
  "errors": [],
  "timestamp": "2026-02-07T12:00:00Z"
}
```

#### `409` category delete blocked due to linked products
```json
{
  "success": false,
  "message": "Cannot delete category while products are assigned",
  "errors": [],
  "timestamp": "2026-02-07T12:00:00Z"
}
```

### Category query performance checks
- New DB index: `ix_products_category_id` on `products(category_id)`
- Existing DB index: `idx_product_category_active` on `products(category_id, is_active)`
- Example verification query:
```sql
EXPLAIN ANALYZE
SELECT *
FROM products
WHERE category_id = 3 AND is_active = true
ORDER BY id DESC
LIMIT 20;
```

## Returns

The following routes are implemented in `app/api/v1/returns.py` and mounted in `app/main.py`:
- `POST /api/v1/returns/`
- `PUT /api/v1/returns/{return_id}/approve`
- `PUT /api/v1/returns/{return_id}/refund`

## Frontend Compatibility Check

Frontend should verify API version on app start:

```javascript
const REQUIRED_API_VERSION = '1.0.0';

async function checkAPICompatibility(API_BASE_URL) {
  const response = await fetch(`${API_BASE_URL}/api/v1/version`, { credentials: 'include' });
  const { version } = await response.json();

  if (version !== REQUIRED_API_VERSION) {
    console.error('API version mismatch!');
    alert('Please refresh the page to get the latest version.');
  }
}
```

## Production Bootstrap Requirements

- `ADMIN_ALLOWED_IPS` must be configured in production.
- At least one active admin user must exist before API startup in production.
- `DEFAULT_ADMIN_PASSWORD` is used only for initial admin seeding via `init_db.py`.

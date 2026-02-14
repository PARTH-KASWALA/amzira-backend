from datetime import datetime
from app.core.config import settings

def order_confirmation_template(order, user):
    """HTML email template for order confirmation"""
    items_html = ""
    for item in order.items:
        items_html += f"""
        <tr>
            <td>{item.product_name} ({item.variant_details})</td>
            <td>{item.quantity}</td>
            <td>₹{item.unit_price:,.2f}</td>
            <td>₹{item.total_price:,.2f}</td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #8B4513; color: white; padding: 20px; text-align: center; }}
            table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            .total {{ font-size: 18px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>AMZIRA</h1>
                <p>Order Confirmation</p>
            </div>
            
            <p>Dear {user.full_name},</p>
            <p>Thank you for your order! Your order <strong>#{order.order_number}</strong> has been confirmed.</p>
            
            <h3>Order Details:</h3>
            <table>
                <thead>
                    <tr>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Price</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    {items_html}
                </tbody>
            </table>
            
            <table>
                <tr>
                    <td>Subtotal:</td>
                    <td>₹{order.subtotal:,.2f}</td>
                </tr>
                <tr>
                    <td>Tax (GST):</td>
                    <td>₹{order.tax_amount:,.2f}</td>
                </tr>
                <tr>
                    <td>Shipping:</td>
                    <td>₹{order.shipping_charge:,.2f}</td>
                </tr>
                <tr class="total">
                    <td>Total:</td>
                    <td>₹{order.total_amount:,.2f}</td>
                </tr>
            </table>
            
            <h3>Shipping Address:</h3>
            <p>
                {order.shipping_address.full_name}<br>
                {order.shipping_address.phone}<br>
                {order.shipping_address.address_line1}<br>
                {order.shipping_address.city}, {order.shipping_address.state} - {order.shipping_address.pincode}
            </p>
            
            <p>Track your order: <a href="https://amzira.com/orders/{order.order_number}">Click here</a></p>
            
            <p>Best regards,<br>Team AMZIRA</p>
        </div>
    </body>
    </html>
    """
    
    return html


def order_shipped_template(order, user, tracking_number: str):
    """HTML email template for order shipped update."""
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2>Good news, {user.full_name}!</h2>
        <p>Your order <strong>#{order.order_number}</strong> has been shipped.</p>
        <p>Tracking Number: <strong>{tracking_number}</strong></p>
        <p>You can track your order here:
            <a href="{settings.FRONTEND_URL}/orders/{order.order_number}">
                Track Order
            </a>
        </p>
        <p>Team AMZIRA</p>
    </body>
    </html>
    """


def order_delivered_template(order, user):
    """HTML email template for order delivered update."""
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2>Order Delivered</h2>
        <p>Hello {user.full_name},</p>
        <p>Your order <strong>#{order.order_number}</strong> has been delivered.</p>
        <p>Thank you for shopping with AMZIRA.</p>
        <p>Team AMZIRA</p>
    </body>
    </html>
    """


def password_reset_template(reset_token: str):
    """HTML email template for password reset."""
    reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"
    current_year = datetime.utcnow().year
    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2>Password Reset Request</h2>
        <p>We received a request to reset your AMZIRA password.</p>
        <p>
            <a href="{reset_link}" style="background:#8B4513;color:#fff;padding:10px 16px;text-decoration:none;border-radius:4px;">
                Reset Password
            </a>
        </p>
        <p>If you did not request this, you can ignore this email.</p>
        <p>This link expires shortly for security reasons.</p>
        <p>&copy; {current_year} AMZIRA</p>
    </body>
    </html>
    """

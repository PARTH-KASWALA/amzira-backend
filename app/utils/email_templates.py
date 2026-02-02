from datetime import datetime

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
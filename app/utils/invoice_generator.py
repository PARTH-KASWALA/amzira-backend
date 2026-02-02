from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO
from datetime import datetime


def generate_gst_invoice(order):
    """
    Generate GST-compliant invoice PDF.
    Returns BytesIO buffer.
    """

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    elements = []
    styles = getSampleStyleSheet()

    # -------------------------------
    # COMPANY DETAILS
    # -------------------------------
    company_info = [
        ["AMZIRA FASHION LLP"],
        ["123 Fashion Street, Mumbai - 400001"],
        ["GSTIN: 27AAACR1234C1Z5"],
        [f"Invoice No: INV-{order.order_number}"],
        [f"Invoice Date: {order.created_at.strftime('%d-%b-%Y')}"],
    ]

    company_table = Table(company_info, colWidths=[6.5 * inch])
    company_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 14),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ]
        )
    )

    elements.append(company_table)
    elements.append(Spacer(1, 0.3 * inch))

    # -------------------------------
    # CUSTOMER DETAILS
    # -------------------------------
    billing = order.billing_address

    customer_info = [
        ["Bill To:"],
        [billing.full_name],
        [billing.address_line1],
        [f"{billing.city}, {billing.state} - {billing.pincode}"],
        [f"Phone: {billing.phone}"],
    ]

    customer_table = Table(customer_info, colWidths=[6.5 * inch])
    customer_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 12),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
            ]
        )
    )

    elements.append(customer_table)
    elements.append(Spacer(1, 0.3 * inch))

    # -------------------------------
    # ITEMS TABLE
    # -------------------------------
    items_data = [
        ["Item", "HSN", "Qty", "Rate", "CGST", "SGST", "Amount"]
    ]

    for item in order.items:
        cgst = item.total_price * 0.09
        sgst = item.total_price * 0.09

        items_data.append(
            [
                item.product_name,
                item.hsn_code or "6104",
                str(item.quantity),
                f"₹{item.unit_price:,.2f}",
                f"₹{cgst:,.2f}",
                f"₹{sgst:,.2f}",
                f"₹{item.total_price:,.2f}",
            ]
        )

    # Totals
    items_data.extend(
        [
            ["", "", "", "", "", "Subtotal:", f"₹{order.subtotal:,.2f}"],
            ["", "", "", "", "", "CGST (9%):", f"₹{order.tax_amount / 2:,.2f}"],
            ["", "", "", "", "", "SGST (9%):", f"₹{order.tax_amount / 2:,.2f}"],
            ["", "", "", "", "", "Shipping:", f"₹{order.shipping_charge:,.2f}"],
            ["", "", "", "", "", "Total:", f"₹{order.total_amount:,.2f}"],
        ]
    )

    items_table = Table(
        items_data,
        colWidths=[
            2.2 * inch,
            0.8 * inch,
            0.5 * inch,
            0.8 * inch,
            0.8 * inch,
            0.8 * inch,
            1 * inch,
        ],
    )

    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )

    elements.append(items_table)
    elements.append(Spacer(1, 0.4 * inch))

    footer = Paragraph(
        "This is a computer-generated invoice. No signature required.",
        styles["Normal"],
    )
    elements.append(footer)

    doc.build(elements)
    buffer.seek(0)

    return buffer


# # Add endpoint to download invoice
# @router.get("/orders/{order_number}/invoice")
# def download_invoice(
#     order_number: str,
#     current_user: User = Depends(get_current_active_user),
#     db: Session = Depends(get_db)
# ):
#     """Download GST invoice"""
#     from fastapi.responses import StreamingResponse
#     from app.utils.invoice_generator import generate_gst_invoice
    
#     order = db.query(Order).filter(
#         Order.order_number == order_number,
#         Order.user_id == current_user.id
#     ).first()
    
#     if not order:
#         raise HTTPException(status_code=404, detail="Order not found")
    
#     if order.status == OrderStatus.PENDING:
#         raise HTTPException(status_code=400, detail="Invoice not available for pending orders")
    
#     pdf = generate_gst_invoice(order)
    
#     return StreamingResponse(
#         pdf,
#         media_type="application/pdf",
#         headers={"Content-Disposition": f"attachment; filename=invoice-{order_number}.pdf"}
#     )
# # ```

# # ---

# # #### **Task 3: Logging & Monitoring**

# # **Install:** Add to `requirements.txt`:
# # ```
# # structlog==24.1.0
# # sentry-sdk[fastapi]==1.40.0
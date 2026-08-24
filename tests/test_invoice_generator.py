from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.utils.invoice_generator import generate_gst_invoice


def test_generate_fully_discounted_invoice_with_decimal_amounts():
    order = SimpleNamespace(
        order_number="AMZ-TEST100",
        created_at=datetime(2026, 8, 17),
        billing_address=SimpleNamespace(
            full_name="Parth Kaswala",
            address_line1="123 Test Street",
            city="Ahmedabad",
            state="Gujarat",
            pincode="380001",
            phone="9876543210",
        ),
        items=[
            SimpleNamespace(
                product_name="Yashvi Navy Maroon Lehenga Choli",
                quantity=3,
                unit_price=Decimal("1449.00"),
                total_price=Decimal("4347.00"),
            )
        ],
        subtotal=Decimal("4347.00"),
        tax_amount=Decimal("217.00"),
        shipping_charge=Decimal("0.00"),
        discount_amount=Decimal("4564.00"),
        coupon_code="PARTH100",
        total_amount=Decimal("0.00"),
    )

    invoice = generate_gst_invoice(order)

    assert invoice.read(4) == b"%PDF"
    assert invoice.getbuffer().nbytes > 1_000

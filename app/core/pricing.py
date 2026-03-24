from decimal import Decimal, ROUND_HALF_UP

from app.core.config import settings

MONEY_QUANTIZER = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def money_float(value) -> float:
    return float(money(value))


def calculate_tax(subtotal) -> Decimal:
    return (money(subtotal) * Decimal(str(settings.GST_RATE))).quantize(
        MONEY_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )


def calculate_shipping(subtotal) -> Decimal:
    subtotal_amount = money(subtotal)
    if subtotal_amount > money(settings.FREE_SHIPPING_THRESHOLD):
        return money(0)
    return money(settings.DEFAULT_SHIPPING_CHARGE)

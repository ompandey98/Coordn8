"""Pricing rules: tax and rounding."""

TAX_RATE = 0.2


def apply_tax(amount, rate=TAX_RATE):
    """Return the amount with tax added."""
    return round_cents(amount * (1 + rate))


def round_cents(value: float) -> float:
    return round(value, 2)

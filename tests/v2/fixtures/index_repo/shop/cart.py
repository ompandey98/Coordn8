"""Shopping cart: holds line items and computes totals."""
import os
from .pricing import apply_tax


class Cart:
    """A basket of line items."""

    def __init__(self):
        self.items = []

    def add_item(self, name, price, qty=1):
        """Add one line item."""
        self.items.append((name, price, qty))
        return self

    def total(self):
        subtotal = sum(p * q for _, p, q in self.items)
        return apply_tax(compute_discount(subtotal))


def compute_discount(subtotal):
    if os.environ.get("NO_DISCOUNT"):
        return subtotal
    return subtotal * 0.95 if subtotal > 100 else subtotal


async def fetch_remote_cart(cart_id: str) -> "Cart":
    return Cart()

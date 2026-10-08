from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CartTotal:
    amount: Decimal
    currency: str


class TotalError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


def calculate_total(items):
    """Add up the lines already priced on the cart.

    The shop stores unit_price on each cart item so a later catalogue change
    does not move the amount the customer agreed to. Tax and shipping are
    not part of this service.
    """
    if not items:
        raise TotalError("empty_cart", "The cart has no products.")

    currencies = {item.product.currency.strip() for item in items}
    if len(currencies) != 1:
        raise TotalError(
            "mixed_currency",
            "Every product in the cart must use the same currency.",
        )

    amount = sum(
        (item.unit_price * item.quantity for item in items),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    if amount <= 0:
        raise TotalError("invalid_amount", "The cart total must be greater than zero.")

    return CartTotal(amount=amount, currency=currencies.pop())

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class ChargeResult:
    approved: bool
    reference: str | None = None
    failure_reason: str | None = None


class MockPaymentProvider:
    """Stand-in for the card processor.

    A token that contains "decline" is refused. Any other token is approved.
    The real provider would take the token from user_payment_methods and
    return its own charge id. We never see the card number.
    """

    def __init__(self):
        self.charges = []

    def charge(self, *, token, amount, currency):
        self.charges.append(
            {"token": token, "amount": amount, "currency": currency}
        )
        if "decline" in token.lower():
            return ChargeResult(approved=False, failure_reason="card_declined")
        return ChargeResult(approved=True, reference="ch_" + uuid4().hex)

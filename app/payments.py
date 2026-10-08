from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.models import Cart, CartItem, Payment, PaymentMethod, Product
from app.totals import TotalError, calculate_total

bp = Blueprint("payments", __name__)

IDEMPOTENCY_KEY_MAX = 255


class PaymentError(Exception):
    def __init__(self, code, message, status_code):
        self.code = code
        self.message = message
        self.status_code = status_code


class PaymentDeclined(Exception):
    def __init__(self, payment):
        self.payment = payment


def _session():
    if "db" not in g:
        g.db = current_app.extensions["session_factory"]()
    return g.db


@bp.teardown_request
def _close_session(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@bp.post("/carts/<uuid:cart_id>/payments")
def start_payment(cart_id):
    user_id, user_error = _user_id_from_header()
    if user_error is not None:
        return user_error

    method_id, method_error = _payment_method_from_body()
    if method_error is not None:
        return method_error

    key, key_error = _idempotency_key()
    if key_error is not None:
        return key_error

    try:
        payment, created = pay_for_cart(
            _session(),
            user_id=user_id,
            cart_id=cart_id,
            payment_method_id=method_id,
            idempotency_key=key,
            provider=current_app.extensions["provider"],
        )
    except PaymentDeclined as exc:
        body = _payment_body(exc.payment)
        body["error"] = "payment_declined"
        body["message"] = "The card was declined."
        return jsonify(body), 402
    except PaymentError as exc:
        return jsonify({"error": exc.code, "message": exc.message}), exc.status_code

    status = 201 if created else 200
    return jsonify(_payment_body(payment)), status


def pay_for_cart(session, *, user_id, cart_id, payment_method_id, idempotency_key, provider):
    cart = session.scalar(select(Cart).where(Cart.id == cart_id).with_for_update())
    if cart is None or cart.user_id != user_id:
        raise PaymentError("cart_not_found", "Cart not found.", 404)

    if idempotency_key:
        existing = session.scalar(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.user_id != user_id or existing.cart_id != cart_id:
                raise PaymentError(
                    "idempotency_key_reused",
                    "This idempotency key was already used for another payment.",
                    409,
                )
            return existing, False

    if cart.status != "active":
        raise PaymentError(
            "cart_not_payable",
            "Only an active cart can be paid.",
            409,
        )

    already_paid = session.scalar(
        select(Payment.id).where(
            Payment.cart_id == cart.id,
            Payment.status == "succeeded",
        )
    )
    if already_paid is not None:
        raise PaymentError(
            "cart_not_payable",
            "Only an active cart can be paid.",
            409,
        )

    items = session.scalars(
        select(CartItem)
        .where(CartItem.cart_id == cart.id)
        .options(joinedload(CartItem.product))
    ).unique().all()

    try:
        total = calculate_total(items)
    except TotalError as exc:
        raise PaymentError(exc.code, exc.message, 422) from exc

    method = _load_payment_method(session, user_id, payment_method_id)
    _reserve_stock(session, items)

    now = datetime.now(timezone.utc)
    payment = Payment(
        id=uuid4(),
        cart_id=cart.id,
        user_id=user_id,
        payment_method_id=method.id,
        amount=total.amount,
        currency=total.currency,
        status="pending",
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )
    session.add(payment)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise PaymentError(
            "payment_conflict",
            "This payment conflicts with an existing one.",
            409,
        ) from exc

    result = provider.charge(
        token=method.provider_token,
        amount=total.amount,
        currency=total.currency,
    )
    payment.updated_at = datetime.now(timezone.utc)

    if not result.approved:
        payment.status = "failed"
        payment.failure_reason = result.failure_reason or "card_declined"
        session.commit()
        raise PaymentDeclined(payment)

    _take_stock(session, items)
    cart.status = "checked_out"
    cart.updated_at = payment.updated_at
    payment.status = "succeeded"
    payment.provider_reference = result.reference
    session.commit()
    return payment, True


def _load_payment_method(session, user_id, payment_method_id):
    if payment_method_id is not None:
        method = session.scalar(
            select(PaymentMethod).where(
                PaymentMethod.id == payment_method_id,
                PaymentMethod.user_id == user_id,
            )
        )
        if method is None:
            raise PaymentError("payment_method_not_found", "Payment method not found.", 404)
        return method

    method = session.scalar(
        select(PaymentMethod)
        .where(PaymentMethod.user_id == user_id, PaymentMethod.is_default.is_(True))
        .order_by(PaymentMethod.created_at.desc())
    )
    if method is None:
        raise PaymentError(
            "payment_method_required",
            "This user has no default payment method.",
            422,
        )
    return method


def _reserve_stock(session, items):
    needed = _quantities_by_product(items)
    products = session.scalars(
        select(Product)
        .where(Product.id.in_(needed))
        .order_by(Product.id)
        .with_for_update()
    ).all()
    by_id = {product.id: product for product in products}
    for product_id, quantity in needed.items():
        product = by_id.get(product_id)
        if product is None or product.stock_quantity < quantity:
            raise PaymentError(
                "insufficient_stock",
                "Not enough stock to pay for this cart.",
                409,
            )


def _take_stock(session, items):
    needed = _quantities_by_product(items)
    products = session.scalars(select(Product).where(Product.id.in_(needed))).all()
    now = datetime.now(timezone.utc)
    for product in products:
        product.stock_quantity -= needed[product.id]
        product.updated_at = now


def _quantities_by_product(items):
    needed = defaultdict(int)
    for item in items:
        needed[item.product_id] += item.quantity
    return needed


def _user_id_from_header():
    raw = request.headers.get("X-User-Id", "").strip()
    if not raw:
        return None, (
            jsonify({"error": "user_required", "message": "X-User-Id header is required."}),
            400,
        )
    try:
        return UUID(raw), None
    except ValueError:
        return None, (
            jsonify({"error": "invalid_user", "message": "X-User-Id must be a UUID."}),
            400,
        )


def _payment_method_from_body():
    if not request.data:
        return None, None
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (
            jsonify({"error": "invalid_body", "message": "Request body must be a JSON object."}),
            400,
        )
    raw = data.get("payment_method_id")
    if raw is None:
        return None, None
    try:
        return UUID(str(raw)), None
    except ValueError:
        return None, (
            jsonify(
                {
                    "error": "invalid_payment_method",
                    "message": "payment_method_id must be a UUID.",
                }
            ),
            400,
        )


def _idempotency_key():
    raw = request.headers.get("Idempotency-Key", "").strip()
    if not raw:
        return None, None
    if len(raw) > IDEMPOTENCY_KEY_MAX:
        return None, (
            jsonify(
                {
                    "error": "invalid_idempotency_key",
                    "message": "Idempotency-Key must be 255 characters or fewer.",
                }
            ),
            400,
        )
    return raw, None


def _payment_body(payment):
    return {
        "id": str(payment.id),
        "cart_id": str(payment.cart_id),
        "status": payment.status,
        "amount": f"{payment.amount:.2f}",
        "currency": payment.currency.strip(),
        "provider_reference": payment.provider_reference,
        "failure_reason": payment.failure_reason,
    }

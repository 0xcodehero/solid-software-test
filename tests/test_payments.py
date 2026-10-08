from decimal import Decimal
from uuid import UUID, uuid4

from app.models import Cart, CartItem, Payment, PaymentMethod, Product, User

ALICE = UUID("11111111-1111-1111-1111-111111111111")
BOB = UUID("22222222-2222-2222-2222-222222222222")
KETTLE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MUG = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CART = UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")
CARD = UUID("11111111-2222-3333-4444-555555555555")


def test_pays_an_active_cart(client, db):
    _alice_cart(db)

    response = _pay(client)

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "succeeded"
    assert body["amount"] == "70.00"
    assert body["currency"] == "USD"
    assert body["cart_id"] == str(CART)
    assert body["provider_reference"].startswith("ch_")
    assert body["failure_reason"] is None

    db.expire_all()
    assert db.get(Cart, CART).status == "checked_out"
    assert db.get(Product, KETTLE).stock_quantity == 9
    assert db.get(Product, MUG).stock_quantity == 98
    assert len(client.application.extensions["provider"].charges) == 1


def test_charges_the_price_stored_on_the_cart_line(client, db):
    _alice_cart(db)
    kettle = db.get(Product, KETTLE)
    kettle.price = Decimal("1.00")
    db.commit()

    response = _pay(client)

    assert response.status_code == 201
    assert response.get_json()["amount"] == "70.00"


def test_declined_card_leaves_the_cart_active(client, db):
    _alice_cart(db, token="tok_decline_alice")

    response = _pay(client)

    assert response.status_code == 402
    body = response.get_json()
    assert body["error"] == "payment_declined"
    assert body["status"] == "failed"
    assert body["failure_reason"] == "card_declined"

    db.expire_all()
    assert db.get(Cart, CART).status == "active"
    assert db.get(Product, KETTLE).stock_quantity == 10
    payment = db.get(Payment, UUID(body["id"]))
    assert payment.status == "failed"


def test_same_idempotency_key_does_not_charge_twice(client, db):
    _alice_cart(db)

    first = _pay(client, key="checkout-alice-1")
    second = _pay(client, key="checkout-alice-1")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.get_json()["id"] == second.get_json()["id"]
    assert len(client.application.extensions["provider"].charges) == 1


def test_retry_after_decline_needs_a_new_key(client, db):
    _alice_cart(db, token="tok_decline_alice")

    declined = _pay(client, key="try-1")
    replay = _pay(client, key="try-1")

    assert declined.status_code == 402
    assert replay.status_code == 200
    assert replay.get_json()["status"] == "failed"
    assert len(client.application.extensions["provider"].charges) == 1


def test_reusing_a_key_for_another_cart_is_rejected(client, db):
    _alice_cart(db)
    bob_cart = uuid4()
    bob_card = uuid4()
    db.add(User(id=BOB, email="bob@example.com", name="Bob"))
    db.add(
        PaymentMethod(
            id=bob_card,
            user_id=BOB,
            provider_token="tok_bob",
            last_four="1111",
            is_default=True,
        )
    )
    db.add(Cart(id=bob_cart, user_id=BOB, status="active"))
    db.add(
        CartItem(
            id=uuid4(),
            cart_id=bob_cart,
            product_id=MUG,
            quantity=1,
            unit_price=Decimal("12.50"),
        )
    )
    db.commit()

    first = _pay(client, key="shared-key")
    second = _pay(client, cart_id=bob_cart, user_id=BOB, key="shared-key")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.get_json()["error"] == "idempotency_key_reused"


def test_a_new_attempt_after_decline_can_succeed(client, db):
    _alice_cart(db, token="tok_decline_alice")

    declined = _pay(client, key="try-1")
    method = db.get(PaymentMethod, CARD)
    method.provider_token = "tok_test_alice_visa"
    db.commit()
    paid = _pay(client, key="try-2")

    assert declined.status_code == 402
    assert paid.status_code == 201
    assert paid.get_json()["status"] == "succeeded"
    db.expire_all()
    assert db.get(Cart, CART).status == "checked_out"
    assert len(client.application.extensions["provider"].charges) == 2


def test_paying_again_without_a_key_is_rejected(client, db):
    _alice_cart(db)

    assert _pay(client).status_code == 201
    again = _pay(client)

    assert again.status_code == 409
    assert again.get_json()["error"] == "cart_not_payable"
    assert len(client.application.extensions["provider"].charges) == 1


def test_empty_cart(client, db):
    db.add(User(id=ALICE, email="alice@example.com", name="Alice"))
    db.add(
        PaymentMethod(
            id=CARD,
            user_id=ALICE,
            provider_token="tok_test_alice_visa",
            last_four="4242",
            is_default=True,
        )
    )
    db.add(Cart(id=CART, user_id=ALICE, status="active"))
    db.commit()

    response = _pay(client)

    assert response.status_code == 422
    assert response.get_json()["error"] == "empty_cart"


def test_someone_elses_cart_looks_missing(client, db):
    _alice_cart(db)

    response = _pay(client, user_id=BOB)

    assert response.status_code == 404
    assert response.get_json()["error"] == "cart_not_found"
    db.expire_all()
    assert db.get(Cart, CART).status == "active"


def test_abandoned_cart_cannot_be_paid(client, db):
    _alice_cart(db, cart_status="abandoned")

    response = _pay(client)

    assert response.status_code == 409
    assert response.get_json()["error"] == "cart_not_payable"


def test_insufficient_stock(client, db):
    _alice_cart(db, kettle_stock=0)

    response = _pay(client)

    assert response.status_code == 409
    assert response.get_json()["error"] == "insufficient_stock"
    assert client.application.extensions["provider"].charges == []
    db.expire_all()
    assert db.get(Cart, CART).status == "active"


def test_missing_user_header(client, db):
    _alice_cart(db)

    response = client.post(f"/carts/{CART}/payments")

    assert response.status_code == 400
    assert response.get_json()["error"] == "user_required"


def test_no_default_card(client, db):
    _alice_cart(db, is_default=False)

    response = _pay(client)

    assert response.status_code == 422
    assert response.get_json()["error"] == "payment_method_required"


def test_can_choose_a_saved_card(client, db):
    _alice_cart(db, token="tok_decline_default")
    backup = uuid4()
    db.add(
        PaymentMethod(
            id=backup,
            user_id=ALICE,
            provider_token="tok_backup_visa",
            last_four="5555",
            is_default=False,
        )
    )
    db.commit()

    response = _pay(client, method_id=backup)

    assert response.status_code == 201
    assert client.application.extensions["provider"].charges[0]["token"] == "tok_backup_visa"


def test_cannot_use_another_users_card(client, db):
    _alice_cart(db)
    bob_card = uuid4()
    db.add(User(id=BOB, email="bob@example.com", name="Bob"))
    db.add(
        PaymentMethod(
            id=bob_card,
            user_id=BOB,
            provider_token="tok_bob",
            last_four="1111",
            is_default=True,
        )
    )
    db.commit()

    response = _pay(client, method_id=bob_card)

    assert response.status_code == 404
    assert response.get_json()["error"] == "payment_method_not_found"


def test_cart_with_two_currencies_is_rejected(client, db):
    _alice_cart(db)
    db.get(Product, MUG).currency = "EUR"
    db.commit()

    response = _pay(client)

    assert response.status_code == 422
    assert response.get_json()["error"] == "mixed_currency"
    assert client.application.extensions["provider"].charges == []


def _pay(client, cart_id=CART, user_id=ALICE, method_id=None, key=None):
    headers = {"X-User-Id": str(user_id)}
    if key is not None:
        headers["Idempotency-Key"] = key
    kwargs = {}
    if method_id is not None:
        kwargs["json"] = {"payment_method_id": str(method_id)}
    return client.post(f"/carts/{cart_id}/payments", headers=headers, **kwargs)


def _alice_cart(
    db,
    *,
    token="tok_test_alice_visa",
    kettle_stock=10,
    cart_status="active",
    is_default=True,
):
    db.add(User(id=ALICE, email="alice@example.com", name="Alice"))
    db.add(
        Product(
            id=KETTLE,
            name="Blue Kettle",
            price=Decimal("45.00"),
            currency="USD",
            stock_quantity=kettle_stock,
        )
    )
    db.add(
        Product(
            id=MUG,
            name="Ceramic Mug",
            price=Decimal("12.50"),
            currency="USD",
            stock_quantity=100,
        )
    )
    db.add(Cart(id=CART, user_id=ALICE, status=cart_status))
    db.add(
        CartItem(
            id=uuid4(),
            cart_id=CART,
            product_id=KETTLE,
            quantity=1,
            unit_price=Decimal("45.00"),
        )
    )
    db.add(
        CartItem(
            id=uuid4(),
            cart_id=CART,
            product_id=MUG,
            quantity=2,
            unit_price=Decimal("12.50"),
        )
    )
    db.add(
        PaymentMethod(
            id=CARD,
            user_id=ALICE,
            provider_token=token,
            last_four="4242",
            is_default=is_default,
        )
    )
    db.commit()

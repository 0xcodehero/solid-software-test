# Payment endpoint

HTTP endpoint that charges a saved card for an active cart.

`POST /carts/<cart_id>/payments`

The shop already has users, products, carts and saved cards. This repo adds the `payments` table and the call that starts a payment.

## Run

PostgreSQL 13 or later. Docker is the easy way:

```bash
docker compose up -d
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop"
Get-Content schema\001_base.sql, schema\002_payments.sql | docker compose exec -T db psql -U shop -d shop
flask --app app run
```

bash:

```bash
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://shop:shop@localhost:5432/shop
cat schema/001_base.sql schema/002_payments.sql | docker compose exec -T db psql -U shop -d shop
flask --app app run
```

`001_base.sql` is the schema that came with the task. Run it on an empty database. `002_payments.sql` is the migration for this task. The base file also inserts Alice, a cart (kettle + two mugs, 70.00 USD) and her Visa token.

```bash
curl -X POST http://127.0.0.1:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H "X-User-Id: 11111111-1111-1111-1111-111111111111" \
  -H "Idempotency-Key: alice-checkout-1"
```

## Tests

The tests create a `shop_test` database on the same Postgres (user `shop`, password `shop`, port 5432), load both SQL files, and wipe the tables between cases.

```bash
pytest
```

Override the URL with `TEST_DATABASE_URL` if needed (`postgresql+psycopg://...`).

## What the endpoint does

The caller sends the signed-in user in `X-User-Id`. There is no login service in this task.

The body is optional:

```json
{ "payment_method_id": "uuid" }
```

If the body is empty, the user's default card is used.

`Idempotency-Key` is optional. The same key returns the payment that was already created and does not charge the card again. A declined charge is stored as `failed`. To try the card again, send a new key, or send no key. Reusing a key for a different cart is rejected.

On success the response is `201` and the cart becomes `checked_out`. Stock is reduced by the quantities on the cart. A repeat charge of that cart is `409`.

A declined card is `402`. The cart stays `active` and stock is left as it was.

## Assumptions

- The amount is the sum of `quantity * unit_price` on the cart lines. The line price is what the customer saw when they added the product. Catalogue price changes after that are ignored. No tax and no shipping.
- Every product in the cart must use the same currency.
- A cart can be paid only while its status is `active`, it has at least one line, and the total is greater than zero.
- The cart must belong to `X-User-Id`. Someone else's cart is `404`.
- The card must belong to that same user. Another user's `payment_method_id` is `404`.
- The provider is a mock. A token that contains `decline` is refused with `card_declined`. Any other token is approved. The sample token `tok_test_alice_visa` succeeds.
- The cart row is locked for the request, and product rows are locked before the charge, so two overlapping checkouts cannot both take the last item or both mark the cart paid.
- The mock runs inside the database transaction. A real processor would need a recorded pending charge and a way to reconcile if we crash after the processor says yes and before we commit. That is out of scope here.
- If a user has more than one default card, the newest one is used.

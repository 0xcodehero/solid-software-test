-- Payment records.
--
-- A cart can have several failed charges. It can have one successful charge.
-- When the client sends an Idempotency-Key, a retry hits the same row and
-- does not charge the card a second time.

CREATE TABLE payments (
    id                 UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id            UUID           NOT NULL REFERENCES carts(id),
    user_id            UUID           NOT NULL REFERENCES users(id),
    payment_method_id  UUID           NOT NULL REFERENCES user_payment_methods(id),
    amount             NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency           CHAR(3)        NOT NULL,
    status             TEXT           NOT NULL
                           CHECK (status IN ('pending', 'succeeded', 'failed')),
    provider_reference TEXT,
    failure_reason     TEXT,
    idempotency_key    TEXT,
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT payments_reason_matches_status CHECK (
        (status = 'failed') = (failure_reason IS NOT NULL)
    ),
    CONSTRAINT payments_reference_when_succeeded CHECK (
        status <> 'succeeded' OR provider_reference IS NOT NULL
    )
);

-- One paid checkout per cart. Failed attempts stay in the table.
CREATE UNIQUE INDEX uq_payments_one_success_per_cart
    ON payments (cart_id)
    WHERE status = 'succeeded';

CREATE UNIQUE INDEX uq_payments_idempotency_key
    ON payments (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_payments_cart_id ON payments (cart_id);
CREATE INDEX idx_payments_user_id ON payments (user_id);

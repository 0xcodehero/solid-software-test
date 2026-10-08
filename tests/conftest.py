import os
import sys
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.provider import MockPaymentProvider
DEFAULT_TEST_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop_test"


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)
    _ensure_database(url)
    _load_schema(url)
    return url


@pytest.fixture
def app(database_url):
    return create_app(database_url)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    session = app.extensions["session_factory"]()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_tables(app):
    app.extensions["provider"] = MockPaymentProvider()
    with app.extensions["engine"].begin() as conn:
        conn.execute(
            text(
                "TRUNCATE payments, cart_items, carts, "
                "user_payment_methods, products, users"
            )
        )


def _ensure_database(sqlalchemy_url):
    name = sqlalchemy_url.rsplit("/", 1)[-1]
    with psycopg.connect(_admin_dsn(sqlalchemy_url), autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (name,),
        ).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{name}"')


def _load_schema(sqlalchemy_url):
    dsn = sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        for filename in ("001_base.sql", "002_payments.sql"):
            script = (ROOT / "schema" / filename).read_text(encoding="utf-8")
            _run_script(conn, script)


def _run_script(conn, script):
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _admin_dsn(sqlalchemy_url):
    base = sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return base.rsplit("/", 1)[0] + "/postgres"

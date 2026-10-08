import os

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.payments import bp
from app.provider import MockPaymentProvider

DEFAULT_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop"


def create_app(database_url=None, provider=None):
    app = Flask(__name__)
    url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_engine(url, pool_pre_ping=True)
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    app.extensions["provider"] = provider or MockPaymentProvider()
    app.register_blueprint(bp)
    return app

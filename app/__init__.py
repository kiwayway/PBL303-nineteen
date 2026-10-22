import os

from flask import Flask


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///:memory:"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if test_config:
        app.config.update(test_config)

    @app.route("/health")
    def health():
        return {"status": "ok"}

    @app.route("/")
    def index():
        return "PBL303 Nineteen - fondasi jalan"

    return app

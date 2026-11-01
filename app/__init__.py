import logging
import os
import sys
from datetime import timedelta

from flask import Flask

from .extensions import db, migrate
from .ratelimit import FailureLimiter

# nilai contoh dari .env.example yang tidak boleh dipakai sungguhan
_WEAK_SECRETS = {"", "dev-only-change-me", "isi-string-acak-panjang"}


def _env_bool(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _setup_logging(app):
    """Log keamanan (fasco.audit) ke stderr agar terbaca docker logs dan Wazuh."""
    logger = logging.getLogger("fasco")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


def create_app(test_config=None):
    testing = bool(test_config and test_config.get("TESTING"))

    secret = os.environ.get("SECRET_KEY", "")
    if not testing and (secret in _WEAK_SECRETS or len(secret) < 32):
        raise RuntimeError(
            "SECRET_KEY belum diatur atau terlalu lemah (minimal 32 karakter). "
            "Isi di .env, contoh: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        if not testing:
            raise RuntimeError("DATABASE_URL belum diatur.")
        db_url = "sqlite:///:memory:"

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret or "test-only-secret",
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # set COOKIE_SECURE=1 setelah situs diakses lewat HTTPS
        SESSION_COOKIE_SECURE=_env_bool("COOKIE_SECURE"),
        LOGIN_MAX_FAILS_PER_ACCOUNT=int(os.environ.get("LOGIN_MAX_FAILS_PER_ACCOUNT", "5")),
        LOGIN_MAX_FAILS_PER_IP=int(os.environ.get("LOGIN_MAX_FAILS_PER_IP", "20")),
        LOGIN_WINDOW_SECONDS=int(os.environ.get("LOGIN_WINDOW_SECONDS", "900")),
        MAIL_SERVER=os.environ.get("MAIL_SERVER", ""),
        MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
        MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "1") == "1",
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", "no-reply@fasco.local"),
    )
    if test_config:
        app.config.update(test_config)

    _setup_logging(app)

    # pembatas login: satu set per app (tes tidak saling mengganggu)
    window = app.config["LOGIN_WINDOW_SECONDS"]
    app.extensions["login_limiter_account"] = FailureLimiter(
        app.config["LOGIN_MAX_FAILS_PER_ACCOUNT"], window)
    app.extensions["login_limiter_ip"] = FailureLimiter(
        app.config["LOGIN_MAX_FAILS_PER_IP"], window)

    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .auth import bp as auth_bp
    from .auth.routes import csrf_token
    from .main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    from .cli import register_cli

    register_cli(app)

    if app.config.get("TESTING"):
        with app.app_context():
            db.create_all()

    return app

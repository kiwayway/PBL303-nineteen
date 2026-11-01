import re

import pytest

from app import create_app
from app.cli import DEFAULT_CATEGORIES
from app.extensions import db
from app.models import Category, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def run(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


ADMIN = ["--name", "Rikky", "--email", "Rikky@Mail.com", "--phone", "0812-3456-7890",
         "--password", "rahasia123"]


def test_create_admin(app):
    r = app.test_cli_runner().invoke(args=["create-admin", *ADMIN])
    assert r.exit_code == 0, r.output
    u = User.query.one()
    assert (u.role, u.email, u.phone, u.is_verified) == ("admin", "rikky@mail.com", "081234567890", True)
    assert u.password_hash != "rahasia123"


def test_create_seller_role(app):
    r = app.test_cli_runner().invoke(args=["create-seller", *ADMIN])
    assert r.exit_code == 0, r.output
    assert User.query.one().role == "seller"


@pytest.mark.parametrize("field,value", [
    ("--email", "bukan-email"), ("--phone", "123"), ("--password", "pendek"), ("--name", "  "),
])
def test_validation_rejected(app, field, value):
    args = list(ADMIN)
    args[args.index(field) + 1] = value
    r = app.test_cli_runner().invoke(args=["create-admin", *args])
    assert r.exit_code != 0
    assert User.query.count() == 0


def test_duplicate_rejected(app):
    app.test_cli_runner().invoke(args=["create-admin", *ADMIN])
    r = app.test_cli_runner().invoke(args=["create-admin", *ADMIN])
    assert r.exit_code != 0 and User.query.count() == 1


def test_seed_categories_idempotent(app):
    app.test_cli_runner().invoke(args=["seed-categories"])
    app.test_cli_runner().invoke(args=["seed-categories"])
    assert Category.query.count() == len(DEFAULT_CATEGORIES)


def _login(client, ident, pw):
    html = client.get("/login").get_data(as_text=True)
    csrf = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    return client.post("/login", data={"identifier": ident, "password": pw, "_csrf": csrf})


def test_admin_can_login(app):
    app.test_cli_runner().invoke(args=["create-admin", *ADMIN])
    r = _login(app.test_client(), "rikky@mail.com", "rahasia123")
    assert r.status_code == 302


def test_inactive_user_cannot_login(app):
    app.test_cli_runner().invoke(args=["create-admin", *ADMIN])
    User.query.one().is_active = False
    db.session.commit()
    r = _login(app.test_client(), "rikky@mail.com", "rahasia123")
    assert r.status_code == 403

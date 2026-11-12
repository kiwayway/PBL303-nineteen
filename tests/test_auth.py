import re

import pytest

from app import create_app
from app.extensions import db
from app.models import OtpCode, User


@pytest.fixture
def client(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "app.auth.routes.send_otp_email",
        lambda email, code, purpose: sent.update(code=code, email=email, purpose=purpose),
    )
    app = create_app({
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    c = app.test_client()
    c.sent = sent
    c.app = app
    return c


def token(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def post(client, path, data, page=None):
    data = dict(data, _csrf=token(client, page or path))
    return client.post(path, data=data)


REG = {"name": "Budi", "email": "Budi@Mail.com", "phone": "0812-3456-7890", "password": "rahasia123"}


def register_and_verify(client):
    r = post(client, "/register", REG)
    assert r.status_code == 302 and r.location.endswith("/verify")
    r = post(client, "/verify", {"code": client.sent["code"]})
    assert r.status_code == 302
    return r


def test_pages_render(client):
    for p in ["/", "/login", "/register", "/forgot-password"]:
        assert client.get(p).status_code == 200


def test_post_without_csrf_rejected(client):
    assert client.post("/login", data={"identifier": "a", "password": "b"}).status_code == 400


def test_register_verify_login_flow(client):
    register_and_verify(client)
    with client.app.app_context():
        u = User.query.one()
        assert u.is_verified and u.email == "budi@mail.com" and u.phone == "081234567890"
        assert u.password_hash != "rahasia123"
    assert b"Halo, <b>Budi</b>" in client.get("/").data
    post(client, "/logout", {}, page="/")
    r = post(client, "/login", {"identifier": "081234567890", "password": "rahasia123"})
    assert r.status_code == 302


def test_wrong_password(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    r = post(client, "/login", {"identifier": "budi@mail.com", "password": "salah-banget"})
    assert r.status_code == 401


def test_wrong_otp_and_lockout(client):
    post(client, "/register", REG)
    for _ in range(5):
        assert post(client, "/verify", {"code": "000000"}, page="/verify").status_code == 400
    # kode benar pun ditolak setelah 5x gagal
    r = post(client, "/verify", {"code": client.sent["code"]}, page="/verify")
    assert r.status_code == 400
    with client.app.app_context():
        assert not User.query.one().is_verified


def test_duplicate_email_rejected(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    r = post(client, "/register", dict(REG, phone="081999999999"))
    assert r.status_code == 409


def test_register_validation(client):
    assert post(client, "/register", dict(REG, password="pendek")).status_code == 400
    assert post(client, "/register", dict(REG, email="bukan-email")).status_code == 400


def test_unverified_login_redirects_to_verify(client):
    post(client, "/register", REG)
    client.get("/logout")
    with client.session_transaction() as s:
        s.clear()
    r = post(client, "/login", {"identifier": "budi@mail.com", "password": "rahasia123"})
    assert r.status_code == 302 and r.location.endswith("/verify")


def test_forgot_and_reset_password(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    r = post(client, "/forgot-password", {"email": "budi@mail.com"})
    assert r.location.endswith("/verify") and client.sent["purpose"] == "reset"
    r = post(client, "/verify", {"code": client.sent["code"]}, page="/verify")
    assert r.location.endswith("/reset-password")
    r = post(client, "/reset-password", {"password": "baruBaru99", "password2": "baruBaru99"})
    assert r.location.endswith("/login")
    r = post(client, "/login", {"identifier": "budi@mail.com", "password": "baruBaru99"})
    assert r.status_code == 302


def test_forgot_unknown_email_same_response(client):
    r = post(client, "/forgot-password", {"email": "tidak-ada@mail.com"})
    assert r.status_code == 302 and r.location.endswith("/verify")
    assert "code" not in client.sent
    assert post(client, "/verify", {"code": "123456"}, page="/verify").status_code == 400


def test_reset_page_needs_otp(client):
    assert client.get("/reset-password").status_code == 302

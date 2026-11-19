"""Kerangka admin: menu aktif, ikon, dan badge jumlah pengajuan seller yang menunggu."""
import pytest

from app import create_app
from app.extensions import db
from app.models import SellerApplication, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def user(role, n):
    u = User(name=f"User {n}", email=f"u{n}@example.com", role=role, is_verified=True)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def admin_client(app):
    admin = user("admin", 0)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = admin.id
    return c


def test_sidebar_marks_current_page_and_has_icons(app):
    html = admin_client(app).get("/admin/users").get_data(as_text=True)
    assert html.count('aria-current="page"') == 1
    assert "ADMIN" in html and "<svg" in html


def test_pending_badge_counts_only_submitted(app):
    for n, status in enumerate(["submitted", "submitted", "approved", "draft"], start=1):
        db.session.add(SellerApplication(user_id=user("customer", n).id, status=status))
    db.session.commit()
    html = admin_client(app).get("/admin/").get_data(as_text=True)
    assert 'title="Menunggu review">2<' in html


def test_no_badge_when_queue_empty(app):
    html = admin_client(app).get("/admin/").get_data(as_text=True)
    assert 'title="Menunggu review"' not in html

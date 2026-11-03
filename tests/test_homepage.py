"""Tes homepage marketplace.

Fixture `app` / `client` di bawah mengasumsikan create_app menerima konfigurasi tes.
Samakan dengan pola di tests/test_health.py bila berbeda.
"""
import pytest

from app import create_app
from app.extensions import db
from app.models import Category, Service, ServicePackage, User


@pytest.fixture()
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed(price=100000, active_package=True):
    seller = User(name="Rikky", email="r@example.com", password_hash="x", role="seller", is_verified=True)
    cat = Category(name="Web dan Pemrograman")
    db.session.add_all([seller, cat])
    db.session.flush()
    svc = Service(seller_id=seller.id, category_id=cat.id, title="Jasa fullstack web", description="d")
    db.session.add(svc)
    db.session.flush()
    db.session.add(ServicePackage(service_id=svc.id, name="Basic", price=price, is_active=active_package))
    db.session.commit()


def test_guest_homepage_shows_profile_icon_linking_to_login(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # Ikon profil tersedia dan mengarah ke halaman login.
    assert 'href="/login"' in html
    assert "Daftar" not in html
    assert "Keluar" not in html



def test_authenticated_homepage_shows_user_and_logout(client, monkeypatch):
    monkeypatch.setattr("app.main.current_user", lambda: User(name="Haikal", email="h@example.com"))
    html = client.get("/").get_data(as_text=True)
    assert "Halo, " in html and "<b>Haikal</b>" in html and "Keluar" in html
    assert 'name="_csrf"' in html
    assert ">Login<" not in html


def test_empty_state_has_no_error(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Belum ada jasa" in r.get_data(as_text=True)


def test_services_and_categories_render(client):
    _seed()
    html = client.get("/").get_data(as_text=True)
    assert "Jasa fullstack web" in html
    assert "Rp100.000" in html
    assert "Web dan Pemrograman" in html


def test_service_without_active_package_hidden(client):
    _seed(active_package=False)
    assert "Jasa fullstack web" not in client.get("/").get_data(as_text=True)

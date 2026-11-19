"""Navbar harus konsisten di semua halaman, dan ringkas: menu pribadi ada di dropdown profil."""
import re
from datetime import datetime

import pytest

from app import create_app
from app.extensions import db
from app.models import Category, Service, ServicePackage, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role, name=None):
    n = User.query.count() + 1
    u = User(name=name or f"Pengguna {n}", email=f"u{n}@example.test", phone=f"08120000{n:04d}", role=role, is_verified=True)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def client_for(app, user=None):
    c = app.test_client()
    if user:
        with c.session_transaction() as s:
            s["user_id"] = user.id
    return c


def utility_bar(html):
    return re.search(r'<div class="hidden bg-mist.*?<header', html, re.S).group(0)


def make_service(seller):
    cat = Category(name="Desain")
    db.session.add(cat)
    db.session.flush()
    svc = Service(seller_id=seller.id, category_id=cat.id, title="Desain logo", description="Deskripsi jasa yang cukup panjang.")
    svc.packages.append(ServicePackage(name="Basic", price=100000, duration_days=2))
    db.session.add(svc)
    db.session.commit()
    return svc


BUYER_PAGES = ["/", "/jasa", "/keranjang", "/checkout", "/akun/", "/akun/pesanan"]
SELLER_PAGES = ["/seller/pesanan", "/seller/jasa", "/seller/keuangan"]


@pytest.mark.parametrize("role,pages", [("customer", BUYER_PAGES), ("seller", BUYER_PAGES + SELLER_PAGES)])
def test_logged_in_navbar_is_present_on_every_page(app, role, pages):
    """Regresi: halaman keranjang/checkout/pembayaran dulu tidak mengirim `user`, jadi navbar tampil seperti tamu."""
    user = make_user(role, "Kiwayway")
    c = client_for(app, user)
    for path in pages:
        html = c.get(path, follow_redirects=True).get_data(as_text=True)
        assert "Halo, <b>Kiwayway</b>" in html, path
        assert ">Masuk<" not in html, path
        assert 'id="profileMenu"' in html, path
        assert 'aria-label="Keranjang"' in html, path                  # ikon keranjang
        assert f"© {datetime.now().year}" in html, path                # footer ikut mendapat tahun


def test_payment_page_keeps_navbar_too(app):
    seller, buyer = make_user("seller"), make_user("customer", "Pembeli")
    svc = make_service(seller)
    c = client_for(app, buyer)
    from app.models import Order, OrderItem
    order = Order(customer_id=buyer.id, status="pending", total=100000)
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderItem(order_id=order.id, package_id=svc.packages[0].id, seller_id=seller.id, price=100000))
    db.session.commit()
    html = c.get(f"/pesanan/{order.id}/pembayaran").get_data(as_text=True)
    assert "Halo, <b>Pembeli</b>" in html and ">Masuk<" not in html


def test_utility_bar_is_minimal(app):
    """Bar atas hanya: Tentang Kami | Hubungi Kami | Halo, Nama. Sisanya ada di dropdown profil."""
    seller = make_user("seller", "Dimas")
    bar = utility_bar(client_for(app, seller).get("/").get_data(as_text=True))
    assert "Tentang Kami" in bar and "Hubungi Kami" in bar and "Halo, <b>Dimas</b>" in bar
    for noisy in ("Keranjang", "Pesanan Saya", "Orderan Saya", "Jasa Saya", "Keuangan", "Keluar", "Dashboard Admin"):
        assert noisy not in bar


def test_guest_utility_bar_shows_login(app):
    html = client_for(app).get("/jasa").get_data(as_text=True)
    bar = utility_bar(html)
    assert "Masuk" in bar and "Halo," not in bar


def menu(html):
    return re.search(r'<details id="profileMenu".*?</details>', html, re.S).group(0)


def test_profile_dropdown_content_per_role(app):
    customer = menu(client_for(app, make_user("customer")).get("/").get_data(as_text=True))
    for label in ("Akun Saya", "Pesanan Saya", "Jadi Seller", "Keluar"):
        assert label in customer
    assert "Orderan Saya" not in customer and "Dashboard Admin" not in customer
    assert 'name="_csrf"' in customer                       # logout tetap form ber-CSRF

    seller = menu(client_for(app, make_user("seller")).get("/").get_data(as_text=True))
    for label in ("Akun Saya", "Pesanan Saya", "Orderan Saya", "Jasa Saya", "Keuangan", "Keluar"):
        assert label in seller
    assert "Jadi Seller" not in seller

    admin_html = client_for(app, make_user("admin")).get("/").get_data(as_text=True)
    admin = menu(admin_html)
    assert "Dashboard Admin" in admin and "Pesanan Saya" not in admin
    assert 'aria-label="Keranjang"' not in admin_html       # admin tidak berbelanja

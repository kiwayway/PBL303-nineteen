"""Seller juga boleh jadi pembeli, tetapi tidak boleh membeli jasanya sendiri."""
import re

import pytest

from app import create_app
from app.extensions import db
from app.models import Cart, CartItem, Category, Order, OrderItem, Service, ServicePackage, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role):
    n = User.query.count() + 1
    u = User(name=f"User {n}", email=f"u{n}@example.test", phone=f"08120000{n:04d}", role=role, is_verified=True)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def login(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user.id
    return c


def csrf(client, path="/jasa"):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def make_service(seller, title="Desain logo"):
    cat = Category.query.first() or Category(name="Desain")
    db.session.add(cat)
    db.session.flush()
    svc = Service(seller_id=seller.id, category_id=cat.id, title=title, description="Deskripsi jasa yang cukup panjang.")
    svc.packages.append(ServicePackage(name="Basic", price=100000, duration_days=2))
    db.session.add(svc)
    db.session.commit()
    return svc, svc.packages[0]


def test_seller_can_buy_another_sellers_service_end_to_end(app):
    seller_a, seller_b = make_user("seller"), make_user("seller")
    svc, pkg = make_service(seller_b)
    c = login(app, seller_a)
    assert c.post(f"/keranjang/tambah/{pkg.id}", data={"_csrf": csrf(c)}).status_code == 302
    assert c.get("/keranjang").status_code == 200
    r = c.post("/checkout", data={"_csrf": csrf(c, "/checkout"), "note": ""})
    assert r.status_code == 302 and "/pembayaran" in r.headers["Location"]
    order = Order.query.one()
    assert order.customer_id == seller_a.id and OrderItem.query.one().seller_id == seller_b.id
    assert c.get(f"/pesanan/{order.id}/pembayaran").status_code == 200
    assert c.get(f"/akun/pesanan/{order.id}").status_code == 200


def test_seller_cannot_add_own_package_to_cart(app):
    seller = make_user("seller")
    svc, pkg = make_service(seller)
    c = login(app, seller)
    r = c.post(f"/keranjang/tambah/{pkg.id}", data={"_csrf": csrf(c)}, follow_redirects=True)
    assert "tidak bisa membeli jasamu sendiri" in r.get_data(as_text=True)
    assert CartItem.query.count() == 0


def test_own_item_already_in_cart_is_purged_and_never_checked_out(app):
    seller = make_user("seller")
    other = make_user("seller")
    _, own_pkg = make_service(seller, "Jasa saya")
    _, other_pkg = make_service(other, "Jasa orang lain")
    cart = Cart(customer_id=seller.id)
    db.session.add(cart)
    db.session.flush()
    db.session.add_all([CartItem(cart_id=cart.id, package_id=own_pkg.id), CartItem(cart_id=cart.id, package_id=other_pkg.id)])
    db.session.commit()
    c = login(app, seller)
    page = c.get("/checkout", follow_redirects=True).get_data(as_text=True)
    assert "dikeluarkan dari keranjang" in page
    c.post("/checkout", data={"_csrf": csrf(c, "/checkout"), "note": ""})
    assert [i.seller_id for i in OrderItem.query.all()] == [other.id]


def test_model_blocks_self_purchase_even_if_routes_are_bypassed(app):
    seller = make_user("seller")
    svc, pkg = make_service(seller)
    order = Order(customer_id=seller.id, status="pending", total=100000)
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderItem(order_id=order.id, package_id=pkg.id, seller_id=seller.id, price=100000))
    with pytest.raises(ValueError, match="sendiri"):
        db.session.flush()
    db.session.rollback()


def test_detail_page_hides_buy_button_for_own_service_only(app):
    seller, other = make_user("seller"), make_user("customer")
    svc, _ = make_service(seller)
    own = login(app, seller).get(f"/jasa/{svc.id}").get_data(as_text=True)
    assert "jasamu sendiri" in own and "Tambah ke keranjang" not in own
    others = login(app, other).get(f"/jasa/{svc.id}").get_data(as_text=True)
    assert "Tambah ke keranjang" in others and "jasamu sendiri" not in others
    guest = app.test_client().get(f"/jasa/{svc.id}").get_data(as_text=True)
    assert "Tambah ke keranjang" in guest


def test_admin_cannot_buy(app):
    admin = make_user("admin")
    seller = make_user("seller")
    _, pkg = make_service(seller)
    c = login(app, admin)
    assert c.post(f"/keranjang/tambah/{pkg.id}", data={"_csrf": csrf(c, "/admin/")}).status_code == 403


def test_seller_navbar_shows_both_buyer_and_seller_menus(app):
    seller = make_user("seller")
    html = login(app, seller).get("/").get_data(as_text=True)
    for label in ("Keranjang", "Pesanan Saya", "Orderan Saya", "Jasa Saya", "Keuangan"):
        assert label in html
    cust_html = login(app, make_user("customer")).get("/").get_data(as_text=True)
    assert "Keranjang" in cust_html and "Orderan Saya" not in cust_html


def test_account_sidebar_for_seller_links_to_seller_tools(app):
    html = login(app, make_user("seller")).get("/akun/").get_data(as_text=True)
    assert "Orderan Saya" in html and "Jasa Saya" in html and "Jadi Seller" not in html


def _buy_forms(html):
    return re.findall(r'<form method="post" action="(/keranjang/tambah/\d+)"', html)


@pytest.mark.parametrize("role", ["customer", "seller"])
def test_detail_buy_form_is_valid_html_and_submittable(app, role):
    """Regresi: form beli pernah rusak (tanda kutip ter-escape) tapi tes teks tetap lolos."""
    owner = make_user("seller")
    svc, pkg = make_service(owner)
    buyer = make_user(role)
    c = login(app, buyer)
    html = c.get(f"/jasa/{svc.id}").get_data(as_text=True)
    assert "&#34;" not in html
    assert _buy_forms(html) == [f"/keranjang/tambah/{pkg.id}"]
    # kirim form persis seperti browser: action dari HTML, token dari HTML
    token = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    r = c.post(_buy_forms(html)[0], data={"_csrf": token})
    assert r.status_code == 302 and r.headers["Location"].endswith("/keranjang")
    assert CartItem.query.count() == 1


def test_detail_has_no_form_for_own_service(app):
    owner = make_user("seller")
    svc, _ = make_service(owner)
    html = login(app, owner).get(f"/jasa/{svc.id}").get_data(as_text=True)
    assert _buy_forms(html) == [] and "&#34;" not in html

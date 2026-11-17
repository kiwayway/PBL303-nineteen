"""Tes halaman profil seller (/seller/<id>) dan kartu seller di homepage."""
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Category, Certificate, Experience, Order, OrderItem, Project, Review, Service,
    ServicePackage, User,
)


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


def _user(name, email, role="seller", **kw):
    u = User(name=name, email=email, password_hash="x", role=role, is_verified=True, **kw)
    db.session.add(u)
    db.session.flush()
    return u


def _full_seller():
    seller = _user("Dimas Pratama", "d@example.com", headline="Full-stack Developer",
                   bio="Suka web aman.", focus="Flask, Docker", location="Batam")
    cust = _user("Sari Wulandari", "s@example.com", role="customer")
    cat = Category(name="Web Development")
    db.session.add(cat)
    db.session.flush()
    db.session.add_all([
        Experience(seller_id=seller.id, position="IT Support", institution="PT Contoh", period="2022"),
        Certificate(seller_id=seller.id, name="Sertifikat Aman", issuer="Polibatam", year=2025,
                    credential_url="javascript:alert(1)"),
        Project(seller_id=seller.id, title="Proyek Ferry", tech_stack="Flask, Docker",
                link="javascript:alert(2)"),
    ])
    svc = Service(seller_id=seller.id, category_id=cat.id, title="Jasa Company Profile", description="d")
    db.session.add(svc)
    db.session.flush()
    pkg = ServicePackage(service_id=svc.id, name="Basic", price=350000)
    db.session.add(pkg)
    db.session.flush()
    order = Order(customer_id=cust.id, status="approved", total=350000)
    db.session.add(order)
    db.session.flush()
    item = OrderItem(order_id=order.id, package_id=pkg.id, seller_id=seller.id,
                     price=350000, status="completed")
    db.session.add(item)
    db.session.flush()
    db.session.add(Review(order_item_id=item.id, customer_id=cust.id, rating=5,
                          comment="Rapi dan tepat waktu"))
    db.session.commit()
    return seller


def test_unknown_seller_is_404(client):
    assert client.get("/seller/999").status_code == 404


def test_customer_has_no_public_profile(client):
    c = _user("Sari", "c@example.com", role="customer")
    db.session.commit()
    assert client.get(f"/seller/{c.id}").status_code == 404


def test_inactive_seller_is_404(client):
    s = _user("Mati", "m@example.com", is_active=False)
    db.session.commit()
    assert client.get(f"/seller/{s.id}").status_code == 404


def test_profile_shows_all_sections(client):
    s = _full_seller()
    r = client.get(f"/seller/{s.id}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for text in ["Dimas Pratama", "Full-stack Developer", "Suka web aman.", "IT Support",
                 "Sertifikat Aman", "Proyek Ferry", "Jasa Company Profile", "Rp350.000",
                 "Rapi dan tepat waktu"]:
        assert text in html
    for tab in ["#tentang", "#pengalaman", "#portofolio", "#jasa", "#review"]:
        assert f'href="{tab}"' in html


def test_review_shows_first_name_only(client):
    s = _full_seller()
    html = client.get(f"/seller/{s.id}").get_data(as_text=True)
    assert "Sari" in html
    assert "Wulandari" not in html


def test_unsafe_links_are_not_rendered(client):
    s = _full_seller()
    html = client.get(f"/seller/{s.id}").get_data(as_text=True)
    assert "javascript:" not in html


def test_empty_profile_has_friendly_messages(client):
    s = _user("Baru", "b@example.com")
    db.session.commit()
    html = client.get(f"/seller/{s.id}").get_data(as_text=True)
    assert "Belum ada pengalaman" in html
    assert "Belum ada proyek" in html
    assert "Belum ada jasa aktif" in html
    assert "Belum ada review" in html


def test_homepage_lists_sellers_linking_to_profile(client):
    s = _full_seller()
    html = client.get("/").get_data(as_text=True)
    assert f'href="/seller/{s.id}"' in html
    assert "Dimas Pratama" in html


def test_homepage_without_sellers_has_empty_state(client):
    assert "Belum ada seller" in client.get("/").get_data(as_text=True)


def test_seed_demo_command_creates_viewable_profile(app, client):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    seller = User.query.filter_by(email="demo.seller@nineteen.local").first()
    assert client.get(f"/seller/{seller.id}").status_code == 200
    # aman dijalankan berulang
    assert "sudah ada" in runner.invoke(args=["seed-demo"]).output

import itertools
import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app import create_app
from app.extensions import db
from app.models import (Category, Order, OrderItem, Review, Service,
                        ServicePackage, User)

_phone = itertools.count(1)


@pytest.fixture
def app():
    app = create_app({"TESTING": True})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role="customer"):
    n = next(_phone)
    u = User(name=f"U{n}", email=f"u{n}@x.com", phone=f"08{n:010d}", role=role)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def seller_with_packages():
    s = make_user("seller")
    c = Category(name=f"Web{s.id}")
    svc = Service(seller=s, category=c, title="Buat web", description="d")
    pk = [ServicePackage(service=svc, name="Basic", price=100000, duration_days=3),
          ServicePackage(service=svc, name="Premium", price=300000, duration_days=7)]
    db.session.add_all([c, svc, *pk])
    db.session.commit()
    return s, svc, pk


def buy(customer, pkg, status="approved", item_status="completed", rating=None):
    o = Order(customer=customer, status=status, total=pkg.price)
    it = OrderItem(order=o, package=pkg, seller_id=pkg.service.seller_id,
                   price=pkg.price, status=item_status)
    db.session.add_all([o, it])
    db.session.commit()
    if rating:
        db.session.add(Review(order_item=it, customer=customer, rating=rating))
        db.session.commit()
    return it


def test_user_defaults(app):
    u = make_user()
    assert u.role == "customer" and u.is_active is True


def test_min_price(app):
    _, svc, _ = seller_with_packages()
    assert svc.min_price == 100000


def test_rating_and_sold_computed(app):
    s, svc, pk = seller_with_packages()
    c = make_user()
    buy(c, pk[0], rating=5)
    buy(c, pk[1], rating=4)
    buy(c, pk[0], status="waiting", item_status="in_progress")  # belum dibayar
    assert svc.avg_rating == 4.5
    assert svc.sold_count == 2
    assert s.completed_orders == 2


def test_top_seller_needs_5_orders_and_rating(app):
    s, _, pk = seller_with_packages()
    c = make_user()
    for _ in range(4):
        buy(c, pk[0], rating=5)
    assert not s.is_top_seller
    buy(c, pk[0], rating=5)
    assert s.is_top_seller


def test_constraints(app):
    s, svc, pk = seller_with_packages()
    c = make_user()
    it = buy(c, pk[0])
    db.session.add(Review(order_item=it, customer=c, rating=6))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    db.session.add(ServicePackage(service=svc, name="Gratis", price=0))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    c.role = "hacker"
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_postgres_ddl_tanpa_constraint_ganda(app):
    """Regresi: constraint ber-nama sama membuat migrasi gagal di Postgres."""
    for table in db.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        names = re.findall(r"CONSTRAINT (\w+)", ddl)
        assert len(names) == len(set(names)), f"{table.name}: {names}"

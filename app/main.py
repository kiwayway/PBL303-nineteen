from datetime import datetime

from flask import Blueprint, render_template
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from .auth.routes import current_user
from .extensions import db
from .models import (
    Category, Order, OrderItem, Review, Service, ServicePackage, User,
)

bp = Blueprint("main", __name__)

CARD_LIMIT = 6        # kartu per bagian (grid 3 kolom x 2 baris)
CATEGORY_TILES = 6    # kartu kategori bergambar di panel hero
CATEGORY_ICONS = 8    # ikon kategori di panel hero


# ---------------------------------------------------------------- filter template
@bp.app_template_filter("rupiah")
def rupiah(value):
    """100000 -> 'Rp100.000'"""
    return "Rp" + f"{int(value or 0):,}".replace(",", ".")


@bp.app_template_filter("media_url")
def media_url(path):
    """URL absolut dipakai apa adanya; selain itu dianggap path relatif terhadap /static."""
    from flask import url_for

    if not path:
        return ""
    if path.startswith(("http://", "https://", "/")):
        return path
    return url_for("static", filename=path)


# ---------------------------------------------------------------- query
def _service_rows(order_by, limit):
    """Satu query untuk kartu jasa: harga minimum, jumlah terjual, dan rating dihitung
    lewat subquery agar tidak terjadi N+1 per kartu."""
    price = (
        db.session.query(
            ServicePackage.service_id.label("sid"),
            func.min(ServicePackage.price).label("min_price"),
        )
        .filter(ServicePackage.is_active.is_(True))
        .group_by(ServicePackage.service_id)
        .subquery()
    )
    sold = (
        db.session.query(
            ServicePackage.service_id.label("sid"),
            func.count(OrderItem.id).label("sold"),
        )
        .join(OrderItem, OrderItem.package_id == ServicePackage.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status == "approved")
        .group_by(ServicePackage.service_id)
        .subquery()
    )
    rating = (
        db.session.query(
            ServicePackage.service_id.label("sid"),
            func.avg(Review.rating).label("rating"),
        )
        .join(OrderItem, OrderItem.package_id == ServicePackage.id)
        .join(Review, Review.order_item_id == OrderItem.id)
        .group_by(ServicePackage.service_id)
        .subquery()
    )
    sold_n = func.coalesce(sold.c.sold, 0)
    rating_n = func.coalesce(rating.c.rating, 0)

    q = (
        db.session.query(Service, price.c.min_price, sold_n, rating.c.rating)
        .join(price, price.c.sid == Service.id)  # hanya jasa yang punya paket aktif
        .join(User, User.id == Service.seller_id)
        .outerjoin(sold, sold.c.sid == Service.id)
        .outerjoin(rating, rating.c.sid == Service.id)
        .filter(Service.is_active.is_(True), User.is_active.is_(True))
        .options(joinedload(Service.seller))
    )
    for key in order_by:
        q = q.order_by(key(price.c.min_price, sold_n, rating_n))
    return [
        {
            "id": s.id,
            "title": s.title,
            "image": s.image,
            "seller_name": s.seller.name,
            "seller_avatar": s.seller.avatar,
            "min_price": int(min_price),
            "sold": int(sold_count),
            "rating": round(float(avg), 1) if avg else None,
        }
        for s, min_price, sold_count, avg in q.limit(limit).all()
    ]


def popular_services(limit=CARD_LIMIT):
    """Paling populer = paling banyak terjual (order approved), lalu rating tertinggi,
    lalu jasa terbaru."""
    return _service_rows(
        [
            lambda p, s, r: s.desc(),
            lambda p, s, r: r.desc(),
            lambda p, s, r: Service.id.desc(),
        ],
        limit,
    )


def affordable_services(limit=CARD_LIMIT):
    """Terjangkau = harga paket aktif termurah (harga 'mulai dari')."""
    return _service_rows(
        [lambda p, s, r: p.asc(), lambda p, s, r: Service.id.desc()],
        limit,
    )


def category_overview():
    """Kategori beserta jumlah jasa aktif dan satu gambar sampul dari jasanya."""
    counts = dict(
        db.session.query(Service.category_id, func.count(Service.id))
        .filter(Service.is_active.is_(True))
        .group_by(Service.category_id)
        .all()
    )
    covers = {}
    for cid, image in (
        db.session.query(Service.category_id, Service.image)
        .filter(Service.is_active.is_(True), Service.image.isnot(None))
        .order_by(Service.id.desc())
        .all()
    ):
        covers.setdefault(cid, image)
    return [
        {"id": c.id, "name": c.name, "count": counts.get(c.id, 0), "cover": covers.get(c.id)}
        for c in Category.query.order_by(Category.name).all()
    ]


# ---------------------------------------------------------------- route
@bp.route("/")
def index():
    categories = category_overview()
    return render_template(
        "index.html",
        user=current_user(),
        now_year=datetime.now().year,
        categories=categories,
        popular=popular_services(),
        affordable=affordable_services(),
        category_icons=categories[:CATEGORY_ICONS],
        category_tiles=categories[:CATEGORY_TILES],
    )


@bp.route("/health")
def health():
    return {"status": "ok"}

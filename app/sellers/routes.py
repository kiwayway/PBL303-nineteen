"""Halaman profil publik seller (gaya LinkedIn): /seller/<id>.

Alurnya: URL -> route ini mengambil data dari database -> dikirim ke template
seller/profile.html yang menampilkannya dalam beberapa tab.
"""
from datetime import datetime

from flask import Blueprint, abort, render_template
from sqlalchemy.orm import joinedload

from ..auth.routes import current_user
from ..extensions import db
from ..models import OrderItem, Review, Service, User

bp = Blueprint("sellers", __name__)

REVIEW_LIMIT = 30


def _service_cards(seller):
    """Jasa aktif milik seller dalam format yang sama dengan kartu di homepage."""
    cards = []
    services = (
        Service.query.filter_by(seller_id=seller.id, is_active=True)
        .order_by(Service.id.desc())
        .all()
    )
    for s in services:
        min_price = s.min_price  # None bila belum punya paket aktif -> jangan ditampilkan
        if not min_price:
            continue
        cards.append(
            {
                "id": s.id,
                "title": s.title,
                "image": s.image,
                "seller_name": seller.name,
                "seller_avatar": seller.avatar,
                "min_price": int(min_price),
                "sold": int(s.sold_count or 0),
                "rating": s.avg_rating,
            }
        )
    return cards


def _reviews(seller):
    """Review untuk semua pesanan seller ini, terbaru dulu."""
    return (
        Review.query.join(OrderItem, Review.order_item_id == OrderItem.id)
        .filter(OrderItem.seller_id == seller.id)
        .options(joinedload(Review.customer))
        .order_by(Review.created_at.desc())
        .limit(REVIEW_LIMIT)
        .all()
    )


@bp.route("/seller/<int:seller_id>")
def profile(seller_id):
    seller = db.session.get(User, seller_id)
    # Hanya akun seller yang aktif yang punya halaman publik.
    if seller is None or seller.role != "seller" or not seller.is_active:
        abort(404)

    reviews = _reviews(seller)
    return render_template(
        "seller/profile.html",
        user=current_user(),
        now_year=datetime.now().year,  # dipakai footer
        seller=seller,
        experiences=list(reversed(seller.experiences)),  # terbaru di atas
        certificates=seller.certificates,
        projects=seller.projects,
        services=_service_cards(seller),
        reviews=reviews,
        review_count=len(reviews),
        page_title=seller.name,
    )

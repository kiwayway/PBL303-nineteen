from datetime import datetime

import logging
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from .auth.routes import current_user
from .extensions import db
from .models import (
    Category, ContactInquiry, Order, OrderItem, Review, Service, ServicePackage, User,
)

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)
CONTACT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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
        {
            "id": c.id,
            "name": c.name,
            "count": counts.get(c.id, 0),
            "logo": c.logo_image,
            "cover": c.background_image or covers.get(c.id),
        }
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


@bp.route("/about")
def about():
    return render_template("about.html", user=current_user(), now_year=datetime.now().year)


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        # Honeypot sederhana untuk mengurangi spam bot.
        if request.form.get("website", "").strip():
            flash("Pesan berhasil dikirim. Terima kasih sudah menghubungi Nineteen.", "success")
            return redirect(url_for("main.contact"))

        name = " ".join(request.form.get("name", "").split())
        email = request.form.get("email", "").strip().lower()
        subject = " ".join(request.form.get("subject", "").split())
        message = request.form.get("message", "").strip()
        errors = []
        if not 2 <= len(name) <= 120:
            errors.append("Nama harus terdiri dari 2–120 karakter.")
        if len(email) > 254 or not CONTACT_EMAIL_RE.fullmatch(email):
            errors.append("Masukkan alamat email yang valid.")
        if not 3 <= len(subject) <= 120:
            errors.append("Subjek harus terdiri dari 3–120 karakter.")
        if not 10 <= len(message) <= 5000:
            errors.append("Pesan harus terdiri dari 10–5.000 karakter.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("contact.html", user=current_user(), now_year=datetime.now().year,
                                   form_data={"name": name, "email": email, "subject": subject, "message": message}), 400

        inquiry = ContactInquiry(name=name, email=email, subject=subject, message=message)
        db.session.add(inquiry)
        db.session.commit()

        # Pesan tetap tersimpan walaupun SMTP sedang bermasalah.
        try:
            from .mailer import send_contact_email
            send_contact_email(inquiry)
        except Exception:
            log.exception("Pesan Contact Us tersimpan, tetapi notifikasi email gagal dikirim.")
            flash("Pesan tersimpan. Namun notifikasi email belum berhasil dikirim; tim kami tetap dapat melihat pesan Anda.", "warning")
        else:
            flash("Pesan berhasil dikirim. Terima kasih sudah menghubungi Nineteen!", "success")
        return redirect(url_for("main.contact"))

    return render_template("contact.html", user=current_user(), now_year=datetime.now().year, form_data={})

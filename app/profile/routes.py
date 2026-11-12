"""Profile Page customer di /akun: dashboard, data diri, pesanan, alamat, wishlist,
ulasan, dan keamanan akun (ganti password wajib lewat OTP email)."""
import re
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload, selectinload

from ..auditlog import audit
from ..auth.routes import (
    RESEND_COOLDOWN_SECONDS, client_ip, current_user, issue_otp, normalize_phone,
    seconds_since_last_otp,
)
from ..extensions import db
from ..models import (
    Address, Order, OrderItem, OtpCode, Review, Service, ServicePackage, User, WishlistItem,
)
from .avatar import delete_avatar, save_avatar

bp = Blueprint("profile", __name__, url_prefix="/akun")

USERNAME_RE = re.compile(r"[a-z0-9_.]{3,30}")
PHONE_RE = re.compile(r"0\d{8,13}")
ADDRESS_LABELS = ("Rumah", "Kantor", "Lainnya")
MAX_ADDRESSES = 10
ORDERS_PER_PAGE = 8
GENDERS = {"male": "Laki-laki", "female": "Perempuan"}
STATUS_FILTERS = (
    ("all", "Semua"), ("pending", "Menunggu Pembayaran"), ("processing", "Diproses"),
    ("done", "Selesai"), ("cancelled", "Dibatalkan"),
)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


# ---------------------------------------------------------------- guard & helper
@bp.before_request
def _require_login():
    if not current_user():
        return redirect(url_for("auth.login"))


@bp.app_template_filter("tgl")
def tgl(value):
    """datetime/date -> '12 Apr 2025'"""
    if not value:
        return "-"
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def _page(template, active, title, **ctx):
    ctx.setdefault("status_code", 200)
    code = ctx.pop("status_code")
    return render_template(
        template, user=current_user(), now_year=datetime.now().year,
        active=active, page_title=title, **ctx,
    ), code


def _ip():
    return client_ip()


def _safe_next(default):
    """Redirect hanya ke path internal (cegah open redirect)."""
    target = request.form.get("next", "")
    return target if target.startswith("/") and not target.startswith("//") else default


# ---------------------------------------------------------------- pesanan: helper
def order_state(order):
    """Status pembayaran (Order) + pengerjaan (OrderItem) -> (kode filter, label)."""
    if order.status == "rejected":
        return "cancelled", "Dibatalkan"
    if order.status == "pending":
        return "pending", "Menunggu Pembayaran"
    if order.status == "waiting":
        return "pending", "Menunggu Verifikasi"
    if order.items and all(i.status == "completed" for i in order.items):
        return "done", "Selesai"
    return "processing", "Diproses"


def _order_view(order):
    code, label = order_state(order)
    items = []
    for it in order.items:
        svc = it.package.service
        items.append({
            "item": it, "title": svc.title, "image": svc.image, "service_id": svc.id,
            "service_active": svc.is_active and bool(svc.seller and svc.seller.is_active),
            "seller": it.seller.name if it.seller else "-", "package": it.package.name,
            "duration": it.package.duration_days, "price": it.price,
            "done": it.status == "completed", "review": it.review,
        })
    return {
        "order": order, "id": order.id, "no": f"NS-{order.id:05d}", "state": code,
        "label": label, "created_at": order.created_at, "total": order.total,
        "items": items, "title": items[0]["title"] if items else "-",
        "extra": max(len(items) - 1, 0), "image": items[0]["image"] if items else None,
        "seller": items[0]["seller"] if items else "-",
    }


def _customer_orders(user):
    query = (
        Order.query.filter_by(customer_id=user.id)
        .options(
            selectinload(Order.items).options(
                joinedload(OrderItem.package).joinedload(ServicePackage.service)
                .joinedload(Service.seller),
                joinedload(OrderItem.seller),
                joinedload(OrderItem.review),
            )
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
    )
    return [_order_view(o) for o in query.all()]


def _wishlist_rows(user):
    rows = (
        WishlistItem.query.filter_by(user_id=user.id)
        .options(joinedload(WishlistItem.service).joinedload(Service.seller),
                 joinedload(WishlistItem.service).joinedload(Service.category))
        .order_by(WishlistItem.id.desc()).all()
    )
    out = []
    for w in rows:
        s = w.service
        price = s.min_price
        out.append({
            "service": s, "price": price, "rating": s.avg_rating,
            "available": bool(s.is_active and s.seller.is_active and price),
        })
    return out


# ---------------------------------------------------------------- dashboard
@bp.get("/")
def dashboard():
    user = current_user()
    orders = _customer_orders(user)
    counts = {
        "total": len(orders),
        "processing": sum(o["state"] == "processing" for o in orders),
        "done": sum(o["state"] == "done" for o in orders),
        "wishlist": WishlistItem.query.filter_by(user_id=user.id).count(),
    }
    wishlist = [w for w in _wishlist_rows(user) if w["available"]][:3]
    return _page("profile/dashboard.html", "dashboard", "Dashboard",
                 counts=counts, recent=orders[:3], wishlist=wishlist)


# ---------------------------------------------------------------- informasi pribadi
def _info_page(form=None, errors=None, edit=False, status_code=200):
    return _page("profile/info.html", "info", "Informasi Pribadi", form=form or {},
                 errors=errors or {}, edit=edit, genders=GENDERS, status_code=status_code)


@bp.get("/profil")
def info():
    return _info_page(edit=request.args.get("edit") == "1")


@bp.post("/profil")
def info_save():
    user = current_user()
    f = request.form
    name = " ".join(f.get("name", "").split())
    username = f.get("username", "").strip().lower()
    phone = normalize_phone(f.get("phone"))
    birth_raw = f.get("birth_date", "").strip()
    gender = f.get("gender", "").strip()
    errors, birth = {}, None

    if not name:
        errors["name"] = "Nama lengkap tidak boleh kosong."
    elif len(name) > 120:
        errors["name"] = "Nama maksimal 120 karakter."

    if username:
        if not USERNAME_RE.fullmatch(username):
            errors["username"] = "Username 3–30 karakter: huruf kecil, angka, titik, atau garis bawah."
        elif User.query.filter(User.username == username, User.id != user.id).first():
            errors["username"] = "Username sudah dipakai."

    if phone:
        if not PHONE_RE.fullmatch(phone):
            errors["phone"] = "Format nomor telepon tidak valid. Contoh: 081234567890."
        elif User.query.filter(User.phone == phone, User.id != user.id).first():
            errors["phone"] = "Nomor telepon sudah dipakai akun lain."
    elif user.phone:
        errors["phone"] = "Nomor telepon tidak boleh dikosongkan."

    if birth_raw:
        try:
            birth = date.fromisoformat(birth_raw)
            if not date(1900, 1, 1) <= birth <= date.today():
                raise ValueError
        except ValueError:
            errors["birth_date"] = "Tanggal lahir tidak valid."
    if gender and gender not in GENDERS:
        errors["gender"] = "Pilihan jenis kelamin tidak valid."

    if errors:
        return _info_page(form=f, errors=errors, edit=True, status_code=400)

    user.name, user.username, user.phone = name, username or None, phone or None
    user.birth_date, user.gender = birth, gender or None
    db.session.commit()
    audit("profile_update", ip=_ip(), user_id=user.id)
    flash("Perubahan profil berhasil disimpan.", "success")
    return redirect(url_for("profile.info"))


@bp.post("/profil/foto")
def avatar_upload():
    user = current_user()
    upload = request.files.get("avatar")
    if not upload or not upload.filename:
        flash("Pilih foto terlebih dahulu.", "error")
        return redirect(url_for("profile.info"))
    try:
        path = save_avatar(upload)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("profile.info"))
    old, user.avatar = user.avatar, path
    db.session.commit()
    delete_avatar(old)
    audit("avatar_update", ip=_ip(), user_id=user.id)
    flash("Foto profil diperbarui.", "success")
    return redirect(url_for("profile.info"))


@bp.post("/profil/foto/hapus")
def avatar_remove():
    user = current_user()
    old, user.avatar = user.avatar, None
    db.session.commit()
    delete_avatar(old)
    audit("avatar_remove", ip=_ip(), user_id=user.id)
    flash("Foto profil dihapus.", "success")
    return redirect(url_for("profile.info"))


# ---------------------------------------------------------------- pesanan saya
@bp.get("/pesanan")
def orders():
    user = current_user()
    status = request.args.get("status", "all")
    if status not in dict(STATUS_FILTERS):
        status = "all"
    q = request.args.get("q", "").strip()[:80]
    rows = _customer_orders(user)
    tab_counts = {code: sum(1 for o in rows if code == "all" or o["state"] == code)
                  for code, _ in STATUS_FILTERS}
    if status != "all":
        rows = [o for o in rows if o["state"] == status]
    if q:
        needle = q.lower().removeprefix("ns-").lstrip("0")
        rows = [
            o for o in rows
            if q.lower() in o["no"].lower() or (needle.isdigit() and int(needle) == o["id"])
            or any(q.lower() in i["title"].lower() for i in o["items"])
        ]
    total = len(rows)
    pages = max(-(-total // ORDERS_PER_PAGE), 1)
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    rows = rows[(page - 1) * ORDERS_PER_PAGE: page * ORDERS_PER_PAGE]
    return _page("profile/orders.html", "orders", "Pesanan Saya", rows=rows, status=status,
                 q=q, filters=STATUS_FILTERS, tab_counts=tab_counts, page=page, pages=pages,
                 total=total)


@bp.get("/pesanan/<int:order_id>")
def order_detail(order_id):
    user = current_user()
    order = (
        Order.query.filter_by(id=order_id, customer_id=user.id)
        .options(selectinload(Order.items).options(
            joinedload(OrderItem.package).joinedload(ServicePackage.service)
            .joinedload(Service.seller),
            joinedload(OrderItem.seller), joinedload(OrderItem.review)))
        .first_or_404()
    )
    view = _order_view(order)
    done_n = sum(i["done"] for i in view["items"])
    paid = order.status == "approved"
    steps = [
        ("Pesanan dibuat", True),
        ("Pembayaran terverifikasi", paid),
        ("Layanan diproses", paid),
        ("Selesai", view["state"] == "done"),
    ]
    return _page("profile/order_detail.html", "orders", f"Pesanan {view['no']}", o=view,
                 steps=steps, done_n=done_n)


# ---------------------------------------------------------------- alamat saya
def _address_form(f):
    label = f.get("label", "").strip()
    recipient = " ".join(f.get("recipient_name", "").split())
    phone = normalize_phone(f.get("phone"))
    full = f.get("full_address", "").strip()
    notes = f.get("notes", "").strip()
    errors = {}
    if label not in ADDRESS_LABELS:
        errors["label"] = "Pilih label alamat."
    if not 2 <= len(recipient) <= 120:
        errors["recipient_name"] = "Nama penerima 2–120 karakter."
    if not PHONE_RE.fullmatch(phone):
        errors["phone"] = "Format nomor telepon tidak valid. Contoh: 081234567890."
    if not 10 <= len(full) <= 500:
        errors["full_address"] = "Alamat lengkap 10–500 karakter."
    if len(notes) > 255:
        errors["notes"] = "Detail tambahan maksimal 255 karakter."
    return {"label": label, "recipient_name": recipient, "phone": phone,
            "full_address": full, "notes": notes or None}, errors


def _addresses_page(user, *, form=None, errors=None, editing=None, adding=False, status_code=200):
    rows = Address.query.filter_by(user_id=user.id).order_by(
        Address.is_primary.desc(), Address.id).all()
    return _page("profile/addresses.html", "addresses", "Alamat Saya", rows=rows,
                 form=form or {}, errors=errors or {}, editing=editing, adding=adding,
                 labels=ADDRESS_LABELS, has_primary=any(a.is_primary for a in rows),
                 can_add=len(rows) < MAX_ADDRESSES, status_code=status_code)


def _own_address(user, address_id):
    return Address.query.filter_by(id=address_id, user_id=user.id).first_or_404()


@bp.get("/alamat")
def addresses():
    user = current_user()
    edit_id = request.args.get("edit", type=int)
    if edit_id:
        addr = _own_address(user, edit_id)
        form = {c: getattr(addr, c) or "" for c in
                ("label", "recipient_name", "phone", "full_address", "notes")}
        form["is_primary"] = addr.is_primary
        return _addresses_page(user, form=form, editing=addr)
    return _addresses_page(user, form={"recipient_name": user.name, "phone": user.phone or ""},
                           adding=request.args.get("baru") == "1")


@bp.post("/alamat")
def address_create():
    user = current_user()
    if Address.query.filter_by(user_id=user.id).count() >= MAX_ADDRESSES:
        flash(f"Maksimal {MAX_ADDRESSES} alamat tersimpan.", "error")
        return redirect(url_for("profile.addresses"))
    data, errors = _address_form(request.form)
    if errors:
        return _addresses_page(user, form=request.form, errors=errors, adding=True,
                               status_code=400)
    first = Address.query.filter_by(user_id=user.id).count() == 0
    make_primary = first or bool(request.form.get("is_primary"))
    if make_primary:
        Address.query.filter_by(user_id=user.id).update({"is_primary": False})
    addr = Address(user_id=user.id, is_primary=make_primary, **data)
    db.session.add(addr)
    db.session.commit()
    audit("address_create", ip=_ip(), user_id=user.id, address_id=addr.id)
    flash("Alamat baru disimpan.", "success")
    return redirect(url_for("profile.addresses"))


@bp.post("/alamat/<int:address_id>")
def address_update(address_id):
    user = current_user()
    addr = _own_address(user, address_id)
    data, errors = _address_form(request.form)
    if errors:
        return _addresses_page(user, form=request.form, errors=errors, editing=addr,
                               status_code=400)
    for key, value in data.items():
        setattr(addr, key, value)
    if request.form.get("is_primary") and not addr.is_primary:
        Address.query.filter_by(user_id=user.id).update({"is_primary": False})
        addr.is_primary = True
    db.session.commit()
    audit("address_update", ip=_ip(), user_id=user.id, address_id=addr.id)
    flash("Alamat berhasil diperbarui.", "success")
    return redirect(url_for("profile.addresses"))


@bp.post("/alamat/<int:address_id>/utama")
def address_primary(address_id):
    user = current_user()
    addr = _own_address(user, address_id)
    Address.query.filter_by(user_id=user.id).update({"is_primary": False})
    addr.is_primary = True
    db.session.commit()
    audit("address_primary", ip=_ip(), user_id=user.id, address_id=addr.id)
    flash("Alamat utama diperbarui.", "success")
    return redirect(url_for("profile.addresses"))


@bp.post("/alamat/<int:address_id>/hapus")
def address_delete(address_id):
    user = current_user()
    addr = _own_address(user, address_id)
    was_primary = addr.is_primary
    db.session.delete(addr)
    db.session.commit()
    audit("address_delete", ip=_ip(), user_id=user.id, address_id=address_id)
    if was_primary and Address.query.filter_by(user_id=user.id).count():
        flash("Alamat dihapus. Pilih alamat utama yang baru.", "warning")
    else:
        flash("Alamat dihapus.", "success")
    return redirect(url_for("profile.addresses"))


# ---------------------------------------------------------------- wishlist
@bp.get("/wishlist")
def wishlist():
    return _page("profile/wishlist.html", "wishlist", "Wishlist / Favorit",
                 rows=_wishlist_rows(current_user()))


@bp.post("/wishlist/<int:service_id>/tambah")
def wishlist_add(service_id):
    """Dipakai tombol 'Simpan' di halaman daftar/detail jasa."""
    user = current_user()
    service = db.get_or_404(Service, service_id)
    if not service.is_active:
        abort(404)
    if not WishlistItem.query.filter_by(user_id=user.id, service_id=service.id).first():
        db.session.add(WishlistItem(user_id=user.id, service_id=service.id))
        db.session.commit()
        audit("wishlist_add", ip=_ip(), user_id=user.id, service_id=service.id)
    flash("Jasa disimpan ke wishlist.", "success")
    return redirect(_safe_next(url_for("profile.wishlist")))


@bp.post("/wishlist/<int:service_id>/hapus")
def wishlist_remove(service_id):
    user = current_user()
    item = WishlistItem.query.filter_by(user_id=user.id, service_id=service_id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    audit("wishlist_remove", ip=_ip(), user_id=user.id, service_id=service_id)
    flash("Jasa dihapus dari wishlist.", "success")
    return redirect(url_for("profile.wishlist"))


# ---------------------------------------------------------------- ulasan saya
def _reviewable_item(user, item_id):
    """OrderItem milik user, pesanan sudah dibayar (approved) dan pengerjaan selesai."""
    item = (OrderItem.query.join(Order, Order.id == OrderItem.order_id)
            .filter(OrderItem.id == item_id, Order.customer_id == user.id)
            .first_or_404())
    if item.order.status != "approved" or item.status != "completed":
        abort(403)
    return item


def _review_lists(user):
    mine = (
        Review.query.filter_by(customer_id=user.id)
        .options(joinedload(Review.order_item).joinedload(OrderItem.package)
                 .joinedload(ServicePackage.service),
                 joinedload(Review.order_item).joinedload(OrderItem.seller))
        .order_by(Review.created_at.desc(), Review.id.desc()).all()
    )
    pending = (
        OrderItem.query.join(Order, Order.id == OrderItem.order_id)
        .outerjoin(Review, Review.order_item_id == OrderItem.id)
        .filter(Order.customer_id == user.id, Order.status == "approved",
                OrderItem.status == "completed", Review.id.is_(None))
        .options(joinedload(OrderItem.package).joinedload(ServicePackage.service),
                 joinedload(OrderItem.seller))
        .order_by(OrderItem.id.desc()).all()
    )
    return mine, pending


@bp.get("/ulasan")
def reviews():
    user = current_user()
    mine, pending = _review_lists(user)
    form_item = form = None
    form_id = request.args.get("item", type=int)
    if form_id:
        form_item = _reviewable_item(user, form_id)
        if form_item.review:
            form = {"rating": form_item.review.rating, "comment": form_item.review.comment or ""}
    return _page("profile/reviews.html", "reviews", "Ulasan Saya", mine=mine, pending=pending,
                 form_item=form_item, form=form or {}, errors={})


@bp.post("/ulasan/<int:item_id>")
def review_save(item_id):
    user = current_user()
    item = _reviewable_item(user, item_id)
    rating = request.form.get("rating", type=int)
    comment = request.form.get("comment", "").strip()
    errors = {}
    if rating is None or not 1 <= rating <= 5:
        errors["rating"] = "Pilih rating 1–5 bintang."
    if len(comment) > 2000:
        errors["comment"] = "Komentar maksimal 2.000 karakter."
    if errors:
        mine, pending = _review_lists(user)
        return _page("profile/reviews.html", "reviews", "Ulasan Saya", mine=mine,
                     pending=pending, form_item=item, form=request.form, errors=errors,
                     status_code=400)
    review = item.review
    if review:
        review.rating, review.comment = rating, comment or None
        audit("review_update", ip=_ip(), user_id=user.id, review_id=review.id)
    else:
        review = Review(order_item_id=item.id, customer_id=user.id, rating=rating,
                        comment=comment or None)
        db.session.add(review)
        db.session.flush()
        audit("review_create", ip=_ip(), user_id=user.id, review_id=review.id)
    db.session.commit()
    flash("Ulasan disimpan.", "success")
    return redirect(url_for("profile.reviews"))


@bp.post("/ulasan/<int:item_id>/hapus")
def review_delete(item_id):
    user = current_user()
    review = Review.query.filter_by(order_item_id=item_id, customer_id=user.id).first_or_404()
    db.session.delete(review)
    db.session.commit()
    audit("review_delete", ip=_ip(), user_id=user.id, order_item_id=item_id)
    flash("Ulasan dihapus.", "success")
    return redirect(url_for("profile.reviews"))


# ---------------------------------------------------------------- keamanan akun
@bp.get("/keamanan")
def security():
    return _page("profile/security.html", "security", "Keamanan Akun")


@bp.post("/keamanan/ganti-password")
def password_change_start():
    """Ganti password wajib lewat OTP email: pakai ulang alur reset yang sudah ada
    (/verify lalu /reset-password). Tanpa OTP yang valid, /reset-password menolak."""
    user = current_user()
    audit("password_change_requested", ip=_ip(), user_id=user.id)
    session.pop("reset_user_id", None)
    session["pending_user_id"] = user.id
    session["pending_purpose"] = OtpCode.PURPOSE_RESET
    session["pending_email"] = user.email
    session["pending_from"] = "profile"
    if seconds_since_last_otp(user.id, OtpCode.PURPOSE_RESET) >= RESEND_COOLDOWN_SECONDS:
        issue_otp(user, OtpCode.PURPOSE_RESET)
        flash("Kode OTP sudah dikirim ke email kamu.", "success")
    else:
        flash("Kode OTP sebelumnya masih berlaku. Cek email kamu.", "success")
    return redirect(url_for("auth.verify"))

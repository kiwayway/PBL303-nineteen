"""Halaman 'Jadi Seller' di /akun/seller.

Alur: pengguna baru = customer. Untuk jadi seller ia melengkapi syarat (profil, rekening,
kategori, pengalaman/portofolio), lalu mengajukan. Admin yang menyetujui; baru saat itu
`users.role` berubah jadi 'seller' (lihat app/admin/routes.py).
"""
import re
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..auditlog import audit
from ..auth.routes import client_ip, current_user
from ..crypto import encrypt_text
from ..extensions import db
from ..models import (
    Category, Experience, PayoutAccount, Project, SellerApplication, utcnow,
)
from .logic import BANKS, EDITABLE_STATUSES, MIN_PLAN_CHARS, checklist, names_match, progress

bp = Blueprint("seller_apply", __name__, url_prefix="/akun/seller")

MAX_CATEGORIES = 5
MAX_ITEMS = 20
ACCOUNT_RE = re.compile(r"\d{5,20}")


@bp.before_request
def _guard():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role == "admin":
        abort(403)


def _ip():
    return client_ip()


def _clean(value, limit):
    return " ".join((value or "").split())[:limit]


def _editable(user):
    """Form pengajuan hanya bisa diubah customer yang belum 'submitted'/'approved'."""
    app = user.seller_application
    return user.role == "customer" and (app is None or app.status in EDITABLE_STATUSES)


def _can_edit_items(user):
    """Pengalaman/portofolio: customer saat pengajuan terbuka, dan seller kapan saja."""
    return user.role == "seller" or _editable(user)


def _back(msg=None, kind="success", anchor=""):
    if msg:
        flash(msg, kind)
    return redirect(url_for("seller_apply.index") + anchor)


@bp.get("/")
def index():
    user = current_user()
    app = user.seller_application
    payout = user.payout_account
    items = checklist(user, app, payout)
    done, total, percent = progress(items)
    return render_template(
        "seller_apply/index.html", user=user, now_year=datetime.now().year,
        active="seller", page_title="Jadi Seller",
        application=app, payout=payout, items=items, done=done, total=total, percent=percent,
        all_ok=done == total, editable=_editable(user), can_edit_items=_can_edit_items(user),
        banks=BANKS, categories=Category.query.order_by(Category.name).all(),
        selected={c.id for c in app.categories} if app else set(),
        experiences=user.experiences, projects=user.projects,
        max_categories=MAX_CATEGORIES, min_plan=MIN_PLAN_CHARS,
    )


@bp.post("/kategori")
def save_categories():
    user = current_user()
    if not _editable(user):
        return _back("Pengajuan tidak bisa diubah saat ini.", "error")
    ids = {int(i) for i in request.form.getlist("categories") if i.isdigit()}
    cats = Category.query.filter(Category.id.in_(ids)).all() if ids else []
    plan = (request.form.get("service_plan") or "").strip()
    if len(cats) > MAX_CATEGORIES:
        return _back(f"Pilih maksimal {MAX_CATEGORIES} kategori.", "error", "#kategori")
    if len(plan) > 1000:
        return _back("Deskripsi jasa maksimal 1000 karakter.", "error", "#kategori")
    app = user.seller_application
    if app is None:
        app = SellerApplication(user=user, status="draft")
        db.session.add(app)
    app.categories = cats
    app.service_plan = plan
    db.session.commit()
    audit("seller_app_categories_saved", ip=_ip(), user_id=user.id)
    return _back("Kategori dan deskripsi jasa disimpan.", anchor="#kategori")


@bp.post("/rekening")
def save_payout():
    user = current_user()
    if not _editable(user):
        return _back("Rekening tidak bisa diubah saat ini.", "error")
    bank = request.form.get("bank_name", "")
    holder = _clean(request.form.get("account_holder"), 120)
    number = re.sub(r"[\s-]", "", request.form.get("account_number", ""))
    error = None
    if bank not in BANKS:
        error = "Pilih bank yang tersedia."
    elif not holder:
        error = "Nama pemilik rekening wajib diisi."
    elif not names_match(holder, user.name):
        error = "Nama pemilik rekening harus sama dengan nama di akunmu. Perbarui nama di Informasi Pribadi bila perlu."
    elif not ACCOUNT_RE.fullmatch(number):
        error = "Nomor rekening hanya angka, 5 sampai 20 digit."
    if error:
        return _back(error, "error", "#rekening")
    payout = user.payout_account
    if payout is None:
        payout = PayoutAccount(user=user)
        db.session.add(payout)
    payout.bank_name, payout.account_holder = bank, holder
    payout.account_number_enc = encrypt_text(number)
    payout.account_last4 = number[-4:]
    db.session.commit()
    audit("payout_account_saved", ip=_ip(), user_id=user.id, bank=bank)  # nomor TIDAK dicatat
    return _back("Rekening pencairan disimpan.", anchor="#rekening")


@bp.post("/pengalaman")
def add_experience():
    user = current_user()
    if not _can_edit_items(user):
        return _back("Pengajuan sedang ditinjau admin dan tidak bisa diubah.", "error")
    position = _clean(request.form.get("position"), 120)
    institution = _clean(request.form.get("institution"), 120)
    if not position or not institution:
        return _back("Posisi dan nama instansi wajib diisi.", "error", "#pengalaman")
    if len(user.experiences) >= MAX_ITEMS:
        return _back(f"Maksimal {MAX_ITEMS} pengalaman.", "error", "#pengalaman")
    db.session.add(Experience(
        seller_id=user.id, position=position, institution=institution,
        period=_clean(request.form.get("period"), 60) or None,
        description=(request.form.get("description") or "").strip()[:1000] or None,
    ))
    db.session.commit()
    audit("experience_add", ip=_ip(), user_id=user.id)
    return _back("Pengalaman ditambahkan.", anchor="#pengalaman")


@bp.post("/pengalaman/<int:item_id>/hapus")
def delete_experience(item_id):
    user = current_user()
    item = db.session.get(Experience, item_id)
    if item is None or item.seller_id != user.id:
        abort(404)
    if not _can_edit_items(user):
        return _back("Pengajuan sedang ditinjau admin dan tidak bisa diubah.", "error")
    db.session.delete(item)
    db.session.commit()
    audit("experience_delete", ip=_ip(), user_id=user.id, item_id=item_id)
    return _back("Pengalaman dihapus.", anchor="#pengalaman")


@bp.post("/portofolio")
def add_project():
    user = current_user()
    if not _can_edit_items(user):
        return _back("Pengajuan sedang ditinjau admin dan tidak bisa diubah.", "error")
    title = _clean(request.form.get("title"), 160)
    link = (request.form.get("link") or "").strip()
    if not title:
        return _back("Judul proyek wajib diisi.", "error", "#portofolio")
    if link and (len(link) > 255 or not link.lower().startswith(("http://", "https://"))):
        return _back("Tautan proyek harus diawali http:// atau https://.", "error", "#portofolio")
    if len(user.projects) >= MAX_ITEMS:
        return _back(f"Maksimal {MAX_ITEMS} proyek.", "error", "#portofolio")
    db.session.add(Project(
        seller_id=user.id, title=title, link=link or None,
        description=(request.form.get("description") or "").strip()[:1000] or None,
        tech_stack=_clean(request.form.get("tech_stack"), 255) or None,
    ))
    db.session.commit()
    audit("project_add", ip=_ip(), user_id=user.id)
    return _back("Proyek ditambahkan.", anchor="#portofolio")


@bp.post("/portofolio/<int:item_id>/hapus")
def delete_project(item_id):
    user = current_user()
    item = db.session.get(Project, item_id)
    if item is None or item.seller_id != user.id:
        abort(404)
    if not _can_edit_items(user):
        return _back("Pengajuan sedang ditinjau admin dan tidak bisa diubah.", "error")
    db.session.delete(item)
    db.session.commit()
    audit("project_delete", ip=_ip(), user_id=user.id, item_id=item_id)
    return _back("Proyek dihapus.", anchor="#portofolio")


@bp.post("/ajukan")
def submit():
    user = current_user()
    if not _editable(user):
        return _back("Pengajuan tidak bisa dikirim saat ini.", "error")
    app = user.seller_application
    items = checklist(user, app, user.payout_account)
    if not all(i["ok"] for i in items):
        return _back("Lengkapi semua syarat dulu sebelum mengajukan.", "error")
    app.status = "submitted"
    app.submitted_at = utcnow()
    app.admin_note = None
    db.session.commit()
    audit("seller_app_submitted", ip=_ip(), user_id=user.id, application_id=app.id)
    return _back("Pengajuan terkirim. Admin akan meninjaunya.")

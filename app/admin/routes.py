from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from ..auditlog import audit
from ..auth.routes import current_user
from ..extensions import db
from ..models import Category, Order, Review, Service, User
from .category_images import delete_category_image, save_category_image

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAYMENT_STATUSES = {"pending", "waiting", "approved", "rejected"}


@bp.before_request
def require_admin():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role != "admin":
        abort(403)


def _admin_user():
    return current_user()


@bp.get("/")
def dashboard():
    stats = {
        "users": User.query.count(),
        "customers": User.query.filter_by(role="customer").count(),
        "sellers": User.query.filter_by(role="seller").count(),
        "admins": User.query.filter_by(role="admin").count(),
        "services": Service.query.count(),
        "active_services": Service.query.filter_by(is_active=True).count(),
        "orders": Order.query.count(),
        "pending_orders": Order.query.filter(Order.status.in_(("pending", "waiting"))).count(),
        "approved_orders": Order.query.filter_by(status="approved").count(),
        "reviews": Review.query.count(),
        "revenue": db.session.query(func.coalesce(func.sum(Order.total), 0))
            .filter(Order.status == "approved").scalar(),
    }
    recent_users = User.query.order_by(User.created_at.desc(), User.id.desc()).limit(6).all()
    recent_orders = Order.query.order_by(Order.created_at.desc(), Order.id.desc()).limit(6).all()
    return render_template(
        "admin/dashboard.html", stats=stats,
        recent_users=recent_users, recent_orders=recent_orders,
        admin=_admin_user(),
    )


@bp.get("/users")
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    query = User.query
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(User.name.ilike(pattern), User.email.ilike(pattern),
                                 User.phone.ilike(pattern)))
    if role in {"admin", "seller", "customer"}:
        query = query.filter_by(role=role)
    rows = query.order_by(User.created_at.desc(), User.id.desc()).limit(200).all()
    return render_template("admin/users.html", rows=rows, q=q, role=role, admin=_admin_user())


@bp.post("/users/<int:user_id>/toggle")
def toggle_user(user_id):
    target = db.get_or_404(User, user_id)
    actor = _admin_user()
    if target.id == actor.id and target.is_active:
        flash("Kamu tidak dapat menonaktifkan akun sendiri.", "error")
        return redirect(url_for("admin.users"))
    if target.role == "admin" and target.is_active:
        active_admins = User.query.filter_by(role="admin", is_active=True).count()
        if active_admins <= 1:
            flash("Admin aktif terakhir tidak dapat dinonaktifkan.", "error")
            return redirect(url_for("admin.users"))
    target.is_active = not target.is_active
    db.session.commit()
    audit("admin_user_status_changed", user_id=actor.id, target_user_id=target.id,
          is_active=target.is_active)
    flash("Status pengguna berhasil diperbarui.", "success")
    return redirect(url_for("admin.users"))


@bp.get("/services")
def services():
    q = request.args.get("q", "").strip()
    query = Service.query
    if q:
        pattern = f"%{q}%"
        query = query.filter(Service.title.ilike(pattern))
    rows = query.order_by(Service.created_at.desc(), Service.id.desc()).limit(200).all()
    return render_template("admin/services.html", rows=rows, q=q, admin=_admin_user())


@bp.post("/services/<int:service_id>/toggle")
def toggle_service(service_id):
    service = db.get_or_404(Service, service_id)
    service.is_active = not service.is_active
    db.session.commit()
    audit("admin_service_status_changed", user_id=_admin_user().id,
          service_id=service.id, is_active=service.is_active)
    flash("Status jasa berhasil diperbarui.", "success")
    return redirect(url_for("admin.services"))


@bp.get("/orders")
def orders():
    rows = Order.query.order_by(Order.created_at.desc(), Order.id.desc()).limit(200).all()
    return render_template("admin/orders.html", rows=rows, admin=_admin_user())


@bp.get("/reviews")
def reviews():
    rows = Review.query.order_by(Review.created_at.desc(), Review.id.desc()).limit(200).all()
    return render_template("admin/reviews.html", rows=rows, admin=_admin_user())


@bp.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        logo = request.files.get("logo_image")
        background = request.files.get("background_image")
        if not name or len(name) > 80:
            flash("Nama kategori wajib diisi (maksimal 80 karakter).", "error")
            return redirect(url_for("admin.categories"))
        if Category.query.filter(func.lower(Category.name) == name.lower()).first():
            flash("Kategori tersebut sudah ada.", "error")
            return redirect(url_for("admin.categories"))
        category = Category(name=name)
        try:
            if logo and logo.filename:
                category.logo_image = save_category_image(logo, "logo")
            if background and background.filename:
                category.background_image = save_category_image(background, "background")
        except ValueError as exc:
            # Jangan simpan kategori jika salah satu file gagal divalidasi.
            for path in (category.logo_image, category.background_image):
                delete_category_image(path)
            flash(str(exc), "error")
            return redirect(url_for("admin.categories"))
        db.session.add(category)
        db.session.commit()
        audit("admin_category_created", user_id=_admin_user().id, category=name)
        flash("Kategori berhasil ditambahkan.", "success")
        return redirect(url_for("admin.categories"))
    rows = Category.query.order_by(Category.name).all()
    return render_template("admin/categories.html", rows=rows, admin=_admin_user())


@bp.post("/categories/<int:category_id>/images")
def update_category_images(category_id):
    category = db.get_or_404(Category, category_id)
    old_paths = []
    new_paths = []
    try:
        for field, kind in (("logo_image", "logo"), ("background_image", "background")):
            upload = request.files.get(field)
            remove = request.form.get(f"remove_{field}") == "1"
            if upload and upload.filename:
                new_path = save_category_image(upload, kind)
                new_paths.append(new_path)
                old_paths.append(getattr(category, field))
                setattr(category, field, new_path)
            elif remove:
                old_paths.append(getattr(category, field))
                setattr(category, field, None)
    except ValueError as exc:
        for path in new_paths:
            delete_category_image(path)
        flash(str(exc), "error")
        return redirect(url_for("admin.categories"))
    db.session.commit()
    for path in old_paths:
        delete_category_image(path)
    audit("admin_category_images_updated", user_id=_admin_user().id, category_id=category.id)
    flash("Gambar kategori berhasil diperbarui.", "success")
    return redirect(url_for("admin.categories"))

@bp.post("/categories/<int:category_id>/delete")
def delete_category(category_id):
    category = db.get_or_404(Category, category_id)
    if Service.query.filter_by(category_id=category.id).first():
        flash("Kategori tidak dapat dihapus karena masih digunakan oleh jasa.", "error")
    else:
        name = category.name
        logo_path, background_path = category.logo_image, category.background_image
        db.session.delete(category)
        db.session.commit()
        delete_category_image(logo_path)
        delete_category_image(background_path)
        audit("admin_category_deleted", user_id=_admin_user().id, category=name)
        flash("Kategori berhasil dihapus.", "success")
    return redirect(url_for("admin.categories"))

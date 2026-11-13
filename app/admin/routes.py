import io
import uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, or_

from ..auditlog import audit
from ..auth.routes import current_user
from ..extensions import db
from ..models import Category, Order, Review, Service, ServicePackage, User
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
    if target.email.endswith("@account.invalid"):
        flash("Akun yang sudah dianonimkan tidak dapat diaktifkan kembali.","error")
        return redirect(url_for("admin.users"))
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



def _service_image(upload):
    """Validate, decode, and re-encode service images safely."""
    if not upload or not upload.filename:
        return None

    max_bytes = 4 * 1024 * 1024
    max_pixels = 20_000_000
    allowed_formats = {
        "JPEG": "jpg",
        "PNG": "png",
        "WEBP": "webp",
    }

    raw = upload.stream.read(max_bytes + 1)

    if not raw:
        raise ValueError("Pilih gambar terlebih dahulu.")

    if len(raw) > max_bytes:
        raise ValueError("Gambar maksimal 4 MB.")

    try:
        # First pass: identify the real format and verify file integrity.
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = probe.format

            if image_format not in allowed_formats:
                raise ValueError("Gunakan gambar PNG, JPEG, atau WebP.")

            if probe.width * probe.height > max_pixels:
                raise ValueError("Dimensi gambar terlalu besar.")

            probe.verify()

        # Second pass: decode pixels and remove metadata by re-encoding.
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)

            if image.mode not in ("RGB", "RGBA"):
                image = image.convert(
                    "RGBA" if "A" in image.getbands() else "RGB"
                )

            if image_format == "JPEG" and image.mode == "RGBA":
                image = image.convert("RGB")

            output = io.BytesIO()
            options = {"optimize": True}

            if image_format == "JPEG":
                options["quality"] = 88

            image.save(output, format=image_format, **options)

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ):
        raise ValueError("File gambar rusak atau tidak valid.") from None

    folder = Path(current_app.static_folder) / "uploads" / "services"
    folder.mkdir(parents=True, exist_ok=True)

    extension = allowed_formats[image_format]
    filename = f"{uuid.uuid4().hex}.{extension}"

    (folder / filename).write_bytes(output.getvalue())

    return f"uploads/services/{filename}"


def _service_form(service=None):
    title=request.form.get("title","").strip(); description=request.form.get("description","").strip()
    category_id=request.form.get("category_id",type=int); active=request.form.get("is_active")=="1"
    seller_id=request.form.get("seller_id",type=int)
    seller=User.query.filter_by(id=seller_id,role="seller").first() if seller_id else None
    name=request.form.get("package_name","").strip(); price=request.form.get("price",type=int); duration=request.form.get("duration_days",type=int)
    category=db.session.get(Category,category_id) if category_id else None; errors=[]
    if not 3<=len(title)<=160: errors.append("Judul jasa wajib 3–160 karakter.")
    if not 10<=len(description)<=10000: errors.append("Deskripsi wajib 10–10.000 karakter.")
    if not category: errors.append("Kategori tidak valid.")
    if not seller: errors.append("Pilih akun seller yang valid.")
    if not service and not 2<=len(name)<=80: errors.append("Nama paket wajib 2–80 karakter.")
    if not service and (price is None or not 1<=price<=1000000000): errors.append("Harga paket tidak valid.")
    if not service and (duration is None or not 1<=duration<=365): errors.append("Durasi harus 1–365 hari.")
    return title,description,category,active,name,price,duration,seller,errors

@bp.route("/services/new",methods=["GET","POST"])
def service_create():
    if request.method=="POST":
        title,description,category,active,name,price,duration,seller,errors=_service_form()
        try: image=_service_image(request.files.get("image"))
        except ValueError as exc: image=None; errors.append(str(exc))
        if errors: flash(" ".join(errors),"error")
        else:
            service=Service(seller_id=seller.id,category_id=category.id,title=title,description=description,is_active=active,image=image)
            service.packages.append(ServicePackage(name=name,price=price,duration_days=duration,is_active=True))
            db.session.add(service); db.session.commit()
            audit("admin_service_created",user_id=_admin_user().id,service_id=service.id)
            flash("Jasa dan paket berhasil dibuat.","success"); return redirect(url_for("admin.services"))
    return render_template("admin/service_form.html",service=None,categories=Category.query.order_by(Category.name).all(),sellers=User.query.filter_by(role="seller",is_active=True).order_by(User.name).all(),admin=_admin_user())

@bp.route("/services/<int:service_id>/edit",methods=["GET","POST"])
def service_edit(service_id):
    service=db.get_or_404(Service,service_id)
    if request.method=="POST":
        title,description,category,active,name,price,duration,seller,errors=_service_form(service)
        try: image=_service_image(request.files.get("image"))
        except ValueError as exc: image=None; errors.append(str(exc))
        if errors: flash(" ".join(errors),"error")
        else:
            service.title=title; service.description=description; service.category_id=category.id; service.seller_id=seller.id; service.is_active=active
            if image: service.image=image
            db.session.commit(); audit("admin_service_updated",user_id=_admin_user().id,service_id=service.id)
            flash("Jasa diperbarui.","success"); return redirect(url_for("admin.services"))
    return render_template("admin/service_form.html",service=service,categories=Category.query.order_by(Category.name).all(),sellers=User.query.filter_by(role="seller",is_active=True).order_by(User.name).all(),admin=_admin_user())

@bp.post("/services/<int:service_id>/packages")
def package_create(service_id):
    service=db.get_or_404(Service,service_id); name=request.form.get("name","").strip()
    price=request.form.get("price",type=int); duration=request.form.get("duration_days",type=int)
    if not 2<=len(name)<=80 or price is None or not 1<=price<=1000000000 or duration is None or not 1<=duration<=365: flash("Data paket tidak valid.","error")
    else:
        service.packages.append(ServicePackage(name=name,description=request.form.get("description","")[:2000],price=price,duration_days=duration,is_active=True))
        db.session.commit(); audit("admin_package_created",user_id=_admin_user().id,service_id=service.id); flash("Paket ditambahkan.","success")
    return redirect(url_for("admin.service_edit",service_id=service.id))

@bp.post("/packages/<int:package_id>/toggle")
def package_toggle(package_id):
    package=db.get_or_404(ServicePackage,package_id); package.is_active=not package.is_active; db.session.commit()
    audit("admin_package_status_changed",user_id=_admin_user().id,package_id=package.id,is_active=package.is_active)
    return redirect(url_for("admin.service_edit",service_id=package.service_id))

@bp.get("/reports")
def reports():
    from datetime import date,datetime,time,timedelta
    start=request.args.get("start",""); end=request.args.get("end",""); query=Order.query
    try:
        if start: query=query.filter(Order.created_at>=datetime.combine(date.fromisoformat(start),time.min))
        if end: query=query.filter(Order.created_at<datetime.combine(date.fromisoformat(end)+timedelta(days=1),time.min))
        if start and end and date.fromisoformat(start)>date.fromisoformat(end): raise ValueError
    except ValueError: flash("Rentang tanggal tidak valid.","error"); query=Order.query
    rows=query.order_by(Order.created_at.desc()).limit(5000).all()
    stats={"orders":len(rows),"approved":sum(o.status=="approved" for o in rows),"pending":sum(o.status in ("pending","waiting") for o in rows),"rejected":sum(o.status=="rejected" for o in rows),"revenue":sum(o.total for o in rows if o.status=="approved")}
    return render_template("admin/reports.html",rows=rows,stats=stats,start=start,end=end,admin=_admin_user())

@bp.get("/reports/export/<kind>")
def report_export(kind):
    from datetime import date,datetime,time,timedelta
    from io import BytesIO
    start=request.args.get("start",""); end=request.args.get("end",""); query=Order.query
    try:
        if start: query=query.filter(Order.created_at>=datetime.combine(date.fromisoformat(start),time.min))
        if end: query=query.filter(Order.created_at<datetime.combine(date.fromisoformat(end)+timedelta(days=1),time.min))
        if start and end and date.fromisoformat(start)>date.fromisoformat(end): abort(400)
    except ValueError: abort(400)
    rows=query.order_by(Order.created_at.desc()).limit(5000).all()
    data=[["ID","Customer ID","Tanggal UTC","Status","Total (IDR)"]]+[[o.id,o.customer_id,o.created_at.isoformat(),o.status,o.total] for o in rows]
    output=BytesIO()
    if kind=="xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb=Workbook(); ws=wb.active; ws.title="Transaksi"
        for row in data: ws.append(row)
        for cell in ws[1]: cell.font=Font(bold=True)
        ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions
        for col,width in {"A":12,"B":14,"C":28,"D":16,"E":20}.items(): ws.column_dimensions[col].width=width
        wb.save(output); mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; filename="laporan-nineteen.xlsx"
    elif kind=="pdf":
        from reportlab.lib.pagesizes import A4,landscape
        from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        doc=SimpleDocTemplate(output,pagesize=landscape(A4)); styles=getSampleStyleSheet()
        table=Table(data,repeatRows=1); table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#2f80ed")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.4,colors.grey),("FONTSIZE",(0,0),(-1,-1),8)]))
        doc.build([Paragraph("Laporan Transaksi Nineteen",styles["Title"]),Spacer(1,12),table])
        mime="application/pdf"; filename="laporan-nineteen.pdf"
    else: abort(404)
    output.seek(0); return send_file(output,as_attachment=True,download_name=filename,mimetype=mime)

@bp.post("/packages/<int:package_id>/edit")
def package_edit(package_id):
    package=db.get_or_404(ServicePackage,package_id)
    name=request.form.get("name","").strip(); description=request.form.get("description","").strip()
    price=request.form.get("price",type=int); duration=request.form.get("duration_days",type=int)
    if not 2<=len(name)<=80 or price is None or not 1<=price<=1000000000 or duration is None or not 1<=duration<=365:
        flash("Data paket tidak valid.","error")
    else:
        package.name=name; package.description=description[:2000]; package.price=price; package.duration_days=duration
        db.session.commit(); audit("admin_package_updated",user_id=_admin_user().id,package_id=package.id)
        flash("Paket diperbarui.","success")
    return redirect(url_for("admin.service_edit",service_id=package.service_id))


@bp.post("/users/<int:user_id>/delete")
def delete_user(user_id):
    target=db.get_or_404(User,user_id); actor=_admin_user()
    if target.id==actor.id:
        flash("Akun yang sedang digunakan tidak dapat dihapus.","error")
        return redirect(url_for("admin.users"))
    if target.role=="admin" and target.is_active and User.query.filter_by(role="admin",is_active=True).count()<=1:
        flash("Admin aktif terakhir tidak dapat dihapus.","error")
        return redirect(url_for("admin.users"))
    # Soft-delete/anonymize: preserve order, payment, review and audit foreign keys.
    target.is_active=False
    target.name="Pengguna dinonaktifkan"
    target.email=f"deleted-{target.id}@account.invalid"
    target.phone=None; target.username=None; target.avatar=None
    target.headline=None; target.bio=None; target.focus=None; target.location=None
    from ..models import Address, WishlistItem, Experience, Certificate, Project, OAuthIdentity, OtpCode
    Address.query.filter_by(user_id=target.id).delete()
    WishlistItem.query.filter_by(user_id=target.id).delete()
    Experience.query.filter_by(seller_id=target.id).delete()
    Certificate.query.filter_by(seller_id=target.id).delete()
    Project.query.filter_by(seller_id=target.id).delete()
    OtpCode.query.filter_by(user_id=target.id).delete()
    OAuthIdentity.query.filter_by(user_id=target.id).update({"email": target.email})
    db.session.commit()
    audit("admin_user_anonymized",user_id=actor.id,target_user_id=target.id)
    flash("Akun dinonaktifkan dan data profil dianonimkan; histori transaksi dipertahankan.","success")
    return redirect(url_for("admin.users"))

import os, secrets, uuid
from datetime import datetime, time, timedelta
from functools import wraps
from pathlib import Path
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError
from .extensions import db
from .models import Cart, CartItem, Order, OrderItem, ServicePackage, Service, User, Category, utcnow
from .auth.routes import current_user
from .auditlog import audit

bp = Blueprint("commerce", __name__)

def login_required(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        user=current_user()
        if not user: return redirect(url_for("auth.login", next=request.path))
        if user.role != "customer": abort(403)
        return fn(*a, **kw)
    return wrapped

def _cart(user):
    cart=Cart.query.filter_by(customer_id=user.id).first()
    if not cart:
        cart=Cart(customer_id=user.id); db.session.add(cart); db.session.flush()
    return cart

@bp.get("/keranjang")
@login_required
def cart():
    c=_cart(current_user())
    items=CartItem.query.filter_by(cart_id=c.id).options(joinedload(CartItem.package).joinedload(ServicePackage.service)).all()
    total=sum(i.package.price for i in items if i.package and i.package.is_active and i.package.service.is_active)
    return render_template("commerce/cart.html", items=items, total=total)

@bp.post("/keranjang/tambah/<int:package_id>")
@login_required
def cart_add(package_id):
    p=ServicePackage.query.join(Service).filter(ServicePackage.id==package_id, ServicePackage.is_active.is_(True), Service.is_active.is_(True)).first_or_404()
    c=_cart(current_user())
    if not CartItem.query.filter_by(cart_id=c.id,package_id=p.id).first():
        db.session.add(CartItem(cart_id=c.id,package_id=p.id)); db.session.commit()
    flash("Paket ditambahkan ke keranjang.","success")
    return redirect(url_for("commerce.cart"))

@bp.post("/keranjang/hapus/<int:item_id>")
@login_required
def cart_remove(item_id):
    c=_cart(current_user()); item=CartItem.query.filter_by(id=item_id,cart_id=c.id).first_or_404()
    db.session.delete(item); db.session.commit()
    flash("Item dihapus.","success"); return redirect(url_for("commerce.cart"))

@bp.route("/checkout", methods=["GET","POST"])
@login_required
def checkout():
    user=current_user(); c=_cart(user)
    items=CartItem.query.filter_by(cart_id=c.id).options(joinedload(CartItem.package).joinedload(ServicePackage.service)).all()
    valid=[i for i in items if i.package and i.package.is_active and i.package.service.is_active and i.package.service.seller.is_active]
    if not valid: flash("Keranjang kosong atau paket tidak lagi tersedia.","error"); return redirect(url_for("commerce.cart"))
    total=sum(i.package.price for i in valid)
    if request.method=="POST":
        customer_note=request.form.get("note","").strip()
        if len(customer_note)>255:
            flash("Catatan maksimal 255 karakter.","error")
            return redirect(url_for("commerce.checkout"))
        order=Order(customer_id=user.id,status="pending",total=total,customer_note=customer_note or None)
        db.session.add(order); db.session.flush()
        for ci in valid:
            p=ci.package
            db.session.add(OrderItem(order_id=order.id,package_id=p.id,seller_id=p.service.seller_id,price=p.price,
                service_title=p.service.title,package_name=p.name,duration_days=p.duration_days))
        for ci in valid: db.session.delete(ci)
        db.session.commit()
        audit("order_created",user_id=user.id,order_id=order.id,total=total)
        return redirect(url_for("commerce.payment",order_id=order.id))
    return render_template("commerce/checkout.html",items=valid,total=total)

@bp.get("/pesanan/<int:order_id>/pembayaran")
@login_required
def payment(order_id):
    order=Order.query.filter_by(id=order_id,customer_id=current_user().id).first_or_404()
    return render_template("commerce/payment.html",order=order,
        bank_name=current_app.config.get("PAYMENT_BANK_NAME",""),
        bank_account_name=current_app.config.get("PAYMENT_BANK_ACCOUNT_NAME",""),
        bank_account_number=current_app.config.get("PAYMENT_BANK_ACCOUNT_NUMBER",""))

@bp.post("/pesanan/<int:order_id>/bukti")
@login_required
def payment_proof(order_id):
    order=Order.query.filter_by(id=order_id,customer_id=current_user().id).first_or_404()
    if order.status not in ("pending","rejected"): abort(409)
    upload=request.files.get("proof")
    if not upload or not upload.filename: flash("Pilih bukti pembayaran.","error"); return redirect(url_for("commerce.payment",order_id=order.id))
    raw=upload.stream.read(5*1024*1024+1); upload.stream.seek(0)
    if len(raw)>5*1024*1024: abort(413)
    ext=None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"): ext=".png"
    elif raw.startswith(b"\xff\xd8\xff"): ext=".jpg"
    elif raw.startswith(b"RIFF") and raw[8:12]==b"WEBP": ext=".webp"
    elif raw.startswith(b"%PDF-"): ext=".pdf"
    else: abort(400,"Format bukti harus JPG, PNG, WebP, atau PDF.")
    if ext in (".png", ".jpg", ".webp"):
        expected_formats = {
            ".png": "PNG",
            ".jpg": "JPEG",
            ".webp": "WEBP",
        }

        try:
            with Image.open(upload.stream) as im:
                if im.format != expected_formats[ext]:
                    abort(400, "Format gambar tidak sesuai dengan isi file.")

                if im.width * im.height > 20_000_000:
                    abort(400, "Dimensi gambar terlalu besar.")

                im.verify()

        except (UnidentifiedImageError, OSError):
            abort(400, "File gambar rusak atau tidak valid.")

        finally:
            upload.stream.seek(0)
    base=Path(current_app.instance_path)/"private_uploads"/"payments"
    base.mkdir(parents=True,exist_ok=True)
    name=secrets.token_hex(24)+ext
    (base/name).write_bytes(raw)
    order.proof_path=name; order.status="waiting"; order.note=None
    db.session.commit(); audit("payment_proof_uploaded",user_id=current_user().id,order_id=order.id)
    flash("Bukti pembayaran dikirim untuk diverifikasi.","success")
    return redirect(url_for("commerce.payment",order_id=order.id))

@bp.get("/admin/orders/<int:order_id>/proof")
def admin_proof(order_id):
    user=current_user()
    if not user: return redirect(url_for("auth.login"))
    if user.role!="admin": abort(403)
    order=db.get_or_404(Order,order_id)
    if not order.proof_path: abort(404)
    base = (
        Path(current_app.instance_path)
        / "private_uploads"
        / "payments"
    ).resolve()

    path = (base / order.proof_path).resolve()

    if path.parent != base or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name="bukti-pembayaran"+path.suffix)

@bp.post("/admin/orders/<int:order_id>/decision")
def admin_decision(order_id):
    user=current_user()
    if not user: return redirect(url_for("auth.login"))
    if user.role!="admin": abort(403)
    order=db.get_or_404(Order,order_id)
    decision=request.form.get("decision")
    reason=request.form.get("reason","").strip()
    if order.status!="waiting" or decision not in ("approve","reject"): abort(409)
    if decision=="approve": order.status="approved"; order.note=None
    else:
        if not 5<=len(reason)<=255: flash("Alasan penolakan 5–255 karakter.","error"); return redirect(url_for("admin.orders"))
        order.status="rejected"; order.note=reason
    db.session.commit(); audit("payment_"+decision,user_id=user.id,order_id=order.id,reason=reason if decision=="reject" else None)
    flash("Keputusan pembayaran tersimpan.","success"); return redirect(url_for("admin.orders"))


@bp.get("/seller/pesanan")
def seller_orders():
    user=current_user()
    if not user: return redirect(url_for("auth.login"))
    if user.role!="seller": abort(403)
    rows=(OrderItem.query.filter_by(seller_id=user.id)
          .options(joinedload(OrderItem.order).joinedload(Order.customer),
                   joinedload(OrderItem.package).joinedload(ServicePackage.service))
          .order_by(OrderItem.id.desc()).limit(200).all())
    return render_template("commerce/seller_orders.html",rows=rows,user=user)

@bp.post("/seller/pesanan/<int:item_id>/selesai")
def seller_complete(item_id):
    user=current_user()
    if not user: return redirect(url_for("auth.login"))
    if user.role!="seller": abort(403)
    item=OrderItem.query.filter_by(id=item_id,seller_id=user.id).first_or_404()
    if item.order.status!="approved" or item.status!="in_progress": abort(409)
    item.status="completed"; item.completed_at=utcnow()
    db.session.commit(); audit("seller_order_item_completed",user_id=user.id,order_item_id=item.id)
    flash("Pengerjaan ditandai selesai.","success")
    return redirect(url_for("commerce.seller_orders"))


# Seller-owned service management. Every object lookup is scoped to the authenticated seller.
def _seller_only():
    user=current_user()
    if not user: return None
    if user.role!="seller": abort(403)
    return user

@bp.get("/seller/jasa")
def seller_services():
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    rows=Service.query.filter_by(seller_id=seller.id).order_by(Service.created_at.desc()).all()
    return render_template("commerce/seller_services.html",rows=rows,user=seller)

@bp.route("/seller/jasa/baru",methods=["GET","POST"])
def seller_service_create():
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    categories=Category.query.order_by(Category.name).all()
    if request.method=="POST":
        title=request.form.get("title","").strip(); description=request.form.get("description","").strip()
        category= db.session.get(Category,request.form.get("category_id",type=int))
        package_name=request.form.get("package_name","").strip(); price=request.form.get("price",type=int)
        duration=request.form.get("duration_days",type=int)
        errors=[]
        if not 3<=len(title)<=160: errors.append("Judul wajib 3–160 karakter.")
        if not 10<=len(description)<=10000: errors.append("Deskripsi wajib 10–10.000 karakter.")
        if not category: errors.append("Kategori tidak valid.")
        if not 2<=len(package_name)<=80: errors.append("Nama paket wajib 2–80 karakter.")
        if price is None or not 1<=price<=1000000000: errors.append("Harga tidak valid.")
        if duration is None or not 1<=duration<=365: errors.append("Durasi tidak valid.")
        image=None
        try:
            from .admin.routes import _service_image
            image=_service_image(request.files.get("image"))
        except ValueError as exc: errors.append(str(exc))
        if errors:
            for error in errors: flash(error,"error")
        else:
            service=Service(seller_id=seller.id,category_id=category.id,title=title,description=description,image=image,is_active=False)
            service.packages.append(ServicePackage(name=package_name,price=price,duration_days=duration,is_active=True))
            db.session.add(service); db.session.commit()
            audit("seller_service_created",user_id=seller.id,service_id=service.id)
            flash("Jasa dibuat sebagai draft. Admin dapat mengaktifkannya setelah pemeriksaan.","success")
            return redirect(url_for("commerce.seller_services"))
    return render_template("commerce/seller_service_form.html",service=None,categories=categories,user=seller)

@bp.route("/seller/jasa/<int:service_id>/edit",methods=["GET","POST"])
def seller_service_edit(service_id):
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    service=Service.query.filter_by(id=service_id,seller_id=seller.id).first_or_404()
    categories=Category.query.order_by(Category.name).all()
    if request.method=="POST":
        title=request.form.get("title","").strip(); description=request.form.get("description","").strip()
        category=db.session.get(Category,request.form.get("category_id",type=int))
        errors=[]
        if not 3<=len(title)<=160: errors.append("Judul wajib 3–160 karakter.")
        if not 10<=len(description)<=10000: errors.append("Deskripsi wajib 10–10.000 karakter.")
        if not category: errors.append("Kategori tidak valid.")
        image=None
        try:
            from .admin.routes import _service_image
            image=_service_image(request.files.get("image"))
        except ValueError as exc: errors.append(str(exc))
        if errors:
            for error in errors: flash(error,"error")
        else:
            service.title=title; service.description=description; service.category_id=category.id
            if image: service.image=image
            db.session.commit(); audit("seller_service_updated",user_id=seller.id,service_id=service.id)
            flash("Jasa diperbarui.","success"); return redirect(url_for("commerce.seller_services"))
    return render_template("commerce/seller_service_form.html",service=service,categories=categories,user=seller)

@bp.post("/seller/jasa/<int:service_id>/paket")
def seller_package_add(service_id):
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    service=Service.query.filter_by(id=service_id,seller_id=seller.id).first_or_404()
    name=request.form.get("name","").strip(); price=request.form.get("price",type=int); duration=request.form.get("duration_days",type=int)
    if not 2<=len(name)<=80 or price is None or not 1<=price<=1000000000 or duration is None or not 1<=duration<=365:
        flash("Data paket tidak valid.","error")
    else:
        service.packages.append(ServicePackage(name=name,description=request.form.get("description","")[:2000],price=price,duration_days=duration,is_active=True))
        db.session.commit(); audit("seller_package_created",user_id=seller.id,service_id=service.id); flash("Paket ditambahkan.","success")
    return redirect(url_for("commerce.seller_service_edit",service_id=service.id))

@bp.post("/seller/paket/<int:package_id>/toggle")
def seller_package_toggle(package_id):
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    package=ServicePackage.query.join(Service).filter(ServicePackage.id==package_id,Service.seller_id==seller.id).first_or_404()
    package.is_active=not package.is_active; db.session.commit()
    audit("seller_package_status_changed",user_id=seller.id,package_id=package.id,is_active=package.is_active)
    return redirect(url_for("commerce.seller_service_edit",service_id=package.service_id))


@bp.post("/seller/jasa/<int:service_id>/arsip")
def seller_service_archive(service_id):
    seller=_seller_only()
    if not seller: return redirect(url_for("auth.login"))
    service=Service.query.filter_by(id=service_id,seller_id=seller.id).first_or_404()
    service.is_active=False
    db.session.commit(); audit("seller_service_archived",user_id=seller.id,service_id=service.id)
    flash("Jasa diarsipkan dan tidak lagi tampil di katalog.","success")
    return redirect(url_for("commerce.seller_services"))

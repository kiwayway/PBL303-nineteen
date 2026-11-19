"""Rute uang: bayar via Midtrans, webhook, kirim hasil, terima hasil, dan keuangan seller."""
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from ..auditlog import audit
from ..auth.routes import client_ip, current_user
from ..commerce import login_required  # khusus customer
from ..models import Order, OrderItem, Payment
from . import midtrans
from .ledger import seller_entries, seller_summary
from .service import (
    FlowError, apply_payment_update, complete_item, deliver_item, start_payment, sync_order_payments,
)

bp = Blueprint("finance", __name__)


def _order_page(order):
    return redirect(url_for("profile.order_detail", order_id=order.id))


def _own_order(order_id):
    return Order.query.filter_by(id=order_id, customer_id=current_user().id).first_or_404()


def _is_midtrans_url(url):
    """Hanya izinkan redirect ke domain Midtrans (mencegah open redirect jika data berubah)."""
    host = urlparse(url or "").hostname or ""
    return urlparse(url or "").scheme == "https" and (host == "midtrans.com" or host.endswith(".midtrans.com"))


def seller_required(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.path))
        if user.role != "seller":
            abort(403)
        return fn(*a, **kw)
    return wrapped


# ---------------------------------------------------------------- pembayaran (buyer)
@bp.post("/pesanan/<int:order_id>/bayar")
@login_required
def pay_start(order_id):
    if not midtrans.enabled():
        abort(404)
    order = _own_order(order_id)
    if order.status != "pending":
        flash("Pesanan ini tidak menunggu pembayaran.", "error")
        return _order_page(order)
    try:
        payment = start_payment(order, current_user())
    except midtrans.MidtransError:
        flash("Pembayaran online sedang tidak tersedia. Coba lagi sebentar lagi.", "error")
        return redirect(url_for("commerce.payment", order_id=order.id))
    if not _is_midtrans_url(payment.redirect_url):
        flash("Tautan pembayaran tidak valid. Hubungi admin.", "error")
        return redirect(url_for("commerce.payment", order_id=order.id))
    return redirect(payment.redirect_url)


def _sync(order):
    """Cek status ke Midtrans. Mengembalikan True bila berhasil menghubungi Midtrans."""
    try:
        sync_order_payments(order)
        return True
    except midtrans.MidtransError:
        return False


@bp.get("/pesanan/<int:order_id>/pembayaran/selesai")
@login_required
def pay_finish(order_id):
    """Tujuan kembali dari halaman Midtrans. Parameter di URL diabaikan; status diambil dari Midtrans."""
    order = _own_order(order_id)
    if midtrans.enabled():
        ok = _sync(order)
        if order.status == "approved":
            flash("Pembayaran diterima. Dana ditahan sistem sampai pekerjaan selesai.", "success")
        elif ok:
            flash("Pembayaran belum selesai. Selesaikan pembayaranmu atau coba lagi.", "warning")
        else:
            flash("Status pembayaran akan diperbarui otomatis. Muat ulang halaman sebentar lagi.", "warning")
    return _order_page(order)


@bp.post("/pesanan/<int:order_id>/pembayaran/cek")
@login_required
def pay_check(order_id):
    if not midtrans.enabled():
        abort(404)
    order = _own_order(order_id)
    ok = _sync(order)
    if order.status == "approved":
        flash("Pembayaran diterima. Dana ditahan sistem sampai pekerjaan selesai.", "success")
    else:
        flash("Belum ada pembayaran yang masuk." if ok else "Gagal menghubungi Midtrans, coba lagi.", "warning")
    return redirect(url_for("commerce.payment", order_id=order.id))


@bp.post("/payments/midtrans/notification")
def midtrans_notification():
    """Webhook Midtrans. Dikecualikan dari CSRF; keasliannya dijamin oleh signature_key."""
    if not midtrans.enabled():
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not midtrans.verify_signature(data):
        audit("midtrans_bad_signature", ip=client_ip())
        return jsonify(error="invalid signature"), 403
    payment = Payment.query.filter_by(midtrans_order_id=str(data.get("order_id"))).first()
    if payment is None:
        return jsonify(status="ignored"), 200  # mis. tes dari dashboard; 200 agar tidak di-retry terus
    try:
        fresh = midtrans.get_status(payment.midtrans_order_id)  # selalu pakai data otoritatif
    except midtrans.MidtransError:
        return jsonify(error="status check failed"), 502  # Midtrans akan mengirim ulang
    if fresh is not None:
        apply_payment_update(payment, fresh)
    return jsonify(status="ok"), 200


# ---------------------------------------------------------------- pengerjaan
@bp.post("/pesanan/item/<int:item_id>/terima")
@login_required
def accept(item_id):
    item = (OrderItem.query.join(Order, Order.id == OrderItem.order_id)
            .filter(OrderItem.id == item_id, Order.customer_id == current_user().id).first_or_404())
    try:
        complete_item(item, "buyer")
    except FlowError as exc:
        flash(str(exc), "error")
    else:
        flash("Hasil diterima. Dana diteruskan ke seller. Jangan lupa beri ulasan!", "success")
    return _order_page(item.order)


@bp.post("/seller/pesanan/<int:item_id>/kirim-hasil")
@seller_required
def deliver(item_id):
    item = OrderItem.query.filter_by(id=item_id, seller_id=current_user().id).first_or_404()
    note = (request.form.get("note") or "").strip()
    url = (request.form.get("url") or "").strip()
    if not 10 <= len(note) <= 1000:
        flash("Catatan pengiriman 10 sampai 1000 karakter.", "error")
    elif url and (len(url) > 255 or not url.lower().startswith(("http://", "https://"))):
        flash("Tautan hasil harus diawali http:// atau https://.", "error")
    else:
        try:
            deliver_item(item, note, url)
            flash("Hasil terkirim. Buyer punya waktu untuk menerima; jika tidak ada respons, pesanan selesai otomatis.", "success")
        except FlowError as exc:
            flash(str(exc), "error")
    return redirect(url_for("commerce.seller_orders"))


# ---------------------------------------------------------------- keuangan seller
@bp.get("/seller/keuangan")
@seller_required
def seller_finance():
    user = current_user()
    return render_template("finance/seller_finance.html", user=user, now_year=datetime.now().year,
                           summary=seller_summary(user.id), entries=seller_entries(user.id))

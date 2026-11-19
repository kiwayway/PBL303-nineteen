"""Alur uang dan pesanan: bayar -> kerjakan -> kirim hasil -> terima -> rilis dana.

Aturan inti escrow:
- Dana buyer dianggap DITAHAN sejak Order berstatus 'approved' (lunas) sampai item 'completed'.
- Hanya BUYER (atau waktu, lewat auto-complete) yang bisa menyelesaikan item. Seller tidak bisa
  menyelesaikan pesanannya sendiri.
- Rilis dana = menulis ledger (record_completion). Dana bisa ditarik setelah masa tunggu.
"""
import json
from datetime import timedelta

from flask import current_app, url_for

from ..auditlog import audit
from ..extensions import db
from ..models import OrderItem, Payment, utcnow
from . import midtrans
from .ledger import record_completion

PENDING_REUSE_HOURS = 20  # token Snap berlaku 24 jam; pakai ulang sebelum mendekati kedaluwarsa


# ---------------------------------------------------------------- pembayaran
def mark_order_paid(order, when=None):
    """Tandai order lunas (dana ditahan) dan mulai hitung batas pengerjaan. Idempoten."""
    if order.status == "approved":
        return False
    when = when or utcnow()
    order.status, order.note, order.paid_at = "approved", None, when
    for item in order.items:
        item.due_at = when + timedelta(days=item.duration_days or 0)
    return True


def _finish_url(order):
    path = url_for("finance.pay_finish", order_id=order.id)
    base = current_app.config.get("PUBLIC_BASE_URL")
    return base + path if base else url_for("finance.pay_finish", order_id=order.id, _external=True)


def start_payment(order, customer):
    """Buat (atau pakai ulang) transaksi Midtrans untuk order. Mengembalikan Payment."""
    cutoff = utcnow() - timedelta(hours=PENDING_REUSE_HOURS)
    for p in reversed(order.payments):
        created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=cutoff.tzinfo)
        if p.status == "pending" and p.redirect_url and created > cutoff:
            return p

    details = [{"id": f"item-{i.id}", "price": i.price, "quantity": 1,
                "name": f"{i.service_title or 'Jasa'} - {i.package_name or 'Paket'}"[:50]}
               for i in order.items]
    if sum(d["price"] for d in details) != order.total:
        raise midtrans.MidtransError("Total pesanan tidak konsisten")

    payment = Payment(order_id=order.id, midtrans_order_id=f"NTN-{order.id}-{len(order.payments) + 1}",
                      gross_amount=order.total)
    db.session.add(payment)
    db.session.commit()
    try:
        token, redirect_url = midtrans.create_snap(
            payment.midtrans_order_id, order.total, customer, details, _finish_url(order))
    except midtrans.MidtransError:
        payment.status, payment.note = "failed", "Gagal membuat transaksi Midtrans"
        db.session.commit()
        raise
    payment.snap_token, payment.redirect_url = token, redirect_url
    db.session.commit()
    audit("payment_started", order_id=order.id, payment=payment.midtrans_order_id, amount=order.total)
    return payment


def apply_payment_update(payment, data):
    """Terapkan status otoritatif dari Midtrans. Aman dipanggil berulang (idempoten)."""
    payment = db.session.query(Payment).filter_by(id=payment.id).with_for_update().one()
    state = midtrans.classify(data)
    payment.transaction_status = data.get("transaction_status")
    payment.fraud_status = data.get("fraud_status")
    payment.payment_type = data.get("payment_type")
    payment.transaction_id = data.get("transaction_id")
    payment.raw_response = json.dumps(data)[:20000]

    if payment.status == "paid":
        if state == "refunded":  # pengembalian dana dari sisi Midtrans: perlu ditangani admin
            payment.status = "refunded"
            audit("payment_refunded_by_gateway", order_id=payment.order_id, payment=payment.midtrans_order_id)
    elif state == "paid":
        if not midtrans.amount_matches(data, payment.gross_amount):
            payment.note = "Nominal dari Midtrans tidak cocok"
            audit("payment_amount_mismatch", order_id=payment.order_id, payment=payment.midtrans_order_id)
        else:
            payment.status, payment.paid_at = "paid", utcnow()
            if payment.order.status == "approved":
                payment.note = "Pembayaran ganda: perlu refund manual"
                audit("payment_duplicate", order_id=payment.order_id, payment=payment.midtrans_order_id)
            else:
                mark_order_paid(payment.order, payment.paid_at)
                audit("payment_paid", order_id=payment.order_id, payment=payment.midtrans_order_id,
                      method=payment.payment_type)
    elif state in ("failed", "expired", "refunded"):
        payment.status = state
    db.session.commit()
    return payment.status


def sync_order_payments(order):
    """Tarik status terbaru dari Midtrans untuk percobaan bayar yang masih menggantung."""
    changed = None
    for p in order.payments:
        if p.status == "pending":
            data = midtrans.get_status(p.midtrans_order_id)
            if data is not None:
                changed = apply_payment_update(p, data)
    return changed


# ---------------------------------------------------------------- pengerjaan
class FlowError(Exception):
    """Aksi tidak diizinkan pada status saat ini."""


def deliver_item(item, note, url=None):
    """Seller mengirim hasil. Mulai hitung mundur auto-complete."""
    if item.order.status != "approved" or item.status != "in_progress":
        raise FlowError("Pesanan belum dibayar atau sudah dikirim.")
    now = utcnow()
    item.status, item.delivered_at = "delivered", now
    item.delivery_note, item.delivery_url = note, url or None
    item.auto_complete_at = now + timedelta(days=current_app.config.get("AUTO_COMPLETE_DAYS", 3))
    db.session.commit()
    audit("item_delivered", order_item_id=item.id, seller_id=item.seller_id)


def complete_item(item, actor, when=None):
    """Selesaikan item dan rilis dana ke ledger. actor: 'buyer' atau 'system'."""
    if item.order.status != "approved" or item.status != "delivered":
        raise FlowError("Hasil belum dikirim atau pesanan sudah selesai.")
    when = when or utcnow()
    item.status, item.completed_at = "completed", when
    record_completion(item, when)
    db.session.commit()
    audit("item_completed", order_item_id=item.id, actor=actor, seller_id=item.seller_id,
          net=item.seller_net, fee=item.platform_fee)


def auto_complete_due(now=None):
    """Selesaikan otomatis semua item yang hasilnya sudah dikirim dan melewati batas. Mengembalikan jumlahnya."""
    now = now or utcnow()
    items = (OrderItem.query.filter(OrderItem.status == "delivered", OrderItem.auto_complete_at <= now)
             .order_by(OrderItem.id).all())
    done = 0
    for item in items:
        try:
            complete_item(item, "system", now)
            done += 1
        except FlowError:
            db.session.rollback()
    return done

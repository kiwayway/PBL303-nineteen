"""Buku besar seller. Saldo SELALU dihitung dari baris ledger dan status pesanan, tidak disimpan."""
from datetime import timedelta

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import LedgerEntry, Order, OrderItem, utcnow


def record_completion(item, when):
    """Rilis dana saat pesanan selesai: earning untuk seller + commission untuk platform.

    Idempoten: dipanggil dua kali pun tidak membuat baris ganda (dijaga juga oleh
    UNIQUE(order_item_id, kind) di database).
    """
    hold = timedelta(days=current_app.config.get("HOLD_DAYS", 3))
    have = {k for (k,) in db.session.query(LedgerEntry.kind).filter_by(order_item_id=item.id)}
    if "earning" not in have:
        db.session.add(LedgerEntry(kind="earning", user_id=item.seller_id, order_item_id=item.id,
                                   amount=item.seller_net, available_at=when + hold))
    if "commission" not in have:
        db.session.add(LedgerEntry(kind="commission", user_id=None, order_item_id=item.id,
                                   amount=item.platform_fee))


def _sum(query):
    return query.scalar() or 0


def seller_summary(user_id, now=None):
    """Angka untuk dashboard seller.

    held      : sudah dibayar buyer, pekerjaan belum selesai (ditahan sistem)
    clearing  : sudah selesai, masih dalam masa tunggu (belum bisa ditarik)
    available : sudah lewat masa tunggu, siap ditarik
    income    : seluruh pendapatan bersih dari pesanan yang selesai
    """
    now = now or utcnow()
    held = _sum(
        db.session.query(func.sum(OrderItem.seller_net))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(OrderItem.seller_id == user_id, Order.status == "approved",
                OrderItem.status.in_(("in_progress", "delivered")))
    )
    earnings = db.session.query(func.sum(LedgerEntry.amount)).filter(
        LedgerEntry.user_id == user_id, LedgerEntry.kind == "earning")
    income = _sum(earnings)
    clearing = _sum(earnings.filter(LedgerEntry.available_at > now))
    return {
        "held": held, "clearing": clearing, "available": income - clearing,
        "income": income, "withdrawn": 0,  # penarikan dana dibuat di langkah 3
    }


def seller_entries(user_id, limit=50):
    return (LedgerEntry.query.filter_by(user_id=user_id, kind="earning")
            .order_by(LedgerEntry.id.desc()).limit(limit).all())

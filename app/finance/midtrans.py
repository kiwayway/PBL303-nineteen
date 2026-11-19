"""Klien Midtrans (Snap + Status API) dan verifikasi webhook.

Prinsip keamanan:
- Server Key hanya dipakai di server (HTTP Basic) dan tidak pernah dicatat atau dikirim ke browser.
- Status pembayaran TIDAK dipercaya dari browser. Yang dipakai hanya hasil Status API
  (server ke server) atau webhook yang signature-nya valid.
"""
import hashlib
import hmac
from decimal import Decimal, InvalidOperation

import requests
from flask import current_app

TIMEOUT = 10  # detik


class MidtransError(Exception):
    """Gagal menghubungi Midtrans atau respons tidak sesuai harapan."""


def enabled():
    return bool(current_app.config.get("MIDTRANS_SERVER_KEY"))


def _urls():
    if current_app.config.get("MIDTRANS_IS_PRODUCTION"):
        return "https://app.midtrans.com/snap/v1/transactions", "https://api.midtrans.com/v2"
    return "https://app.sandbox.midtrans.com/snap/v1/transactions", "https://api.sandbox.midtrans.com/v2"


def _request(method, url, payload=None):
    """Satu-satunya titik yang melakukan HTTP ke Midtrans (mudah di-mock di tes)."""
    try:
        resp = requests.request(
            method, url, json=payload, timeout=TIMEOUT,
            auth=(current_app.config["MIDTRANS_SERVER_KEY"], ""),
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise MidtransError("Tidak bisa menghubungi Midtrans") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def create_snap(midtrans_order_id, gross_amount, customer, item_details, finish_url):
    """Buat transaksi Snap. Mengembalikan (token, redirect_url)."""
    snap_url, _ = _urls()
    payload = {
        "transaction_details": {"order_id": midtrans_order_id, "gross_amount": gross_amount},
        "item_details": item_details,
        "customer_details": {
            "first_name": (customer.name or "Pelanggan")[:50],
            "email": customer.email,
            "phone": customer.phone,
        },
        "callbacks": {"finish": finish_url},
        "expiry": {"unit": "hours", "duration": 24},
    }
    status, body = _request("POST", snap_url, payload)
    if status not in (200, 201) or not body.get("token") or not body.get("redirect_url"):
        raise MidtransError("Midtrans menolak pembuatan transaksi")
    return body["token"], body["redirect_url"]


def get_status(midtrans_order_id):
    """Status otoritatif dari Midtrans. Mengembalikan dict, atau None bila transaksi belum ada."""
    _, api = _urls()
    status, body = _request("GET", f"{api}/{midtrans_order_id}/status")
    if str(body.get("status_code")) == "404" or status == 404:
        return None
    if status != 200 or "transaction_status" not in body:
        raise MidtransError("Respons status Midtrans tidak valid")
    return body


def verify_signature(data):
    """signature_key = SHA512(order_id + status_code + gross_amount + ServerKey)."""
    key = current_app.config.get("MIDTRANS_SERVER_KEY", "")
    try:
        raw = f'{data["order_id"]}{data["status_code"]}{data["gross_amount"]}{key}'
        sent = str(data["signature_key"])
    except KeyError:
        return False
    expected = hashlib.sha512(raw.encode()).hexdigest()
    return hmac.compare_digest(expected, sent)


def classify(data):
    """Terjemahkan status Midtrans ke status internal: paid/pending/failed/expired/refunded."""
    ts = data.get("transaction_status")
    fraud = data.get("fraud_status")
    if ts == "settlement":
        return "paid"
    if ts == "capture":
        return "paid" if fraud == "accept" else "pending"  # 'challenge' = menunggu review fraud
    if ts == "expire":
        return "expired"
    if ts in ("deny", "cancel", "failure"):
        return "failed"
    if ts in ("refund", "partial_refund", "chargeback", "partial_chargeback"):
        return "refunded"
    return "pending"  # pending, authorize, dll.


def amount_matches(data, expected_int):
    try:
        return Decimal(str(data.get("gross_amount"))) == Decimal(expected_int)
    except (InvalidOperation, TypeError):
        return False

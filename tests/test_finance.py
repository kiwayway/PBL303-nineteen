"""Tes uang: komisi, pembayaran Midtrans, webhook, escrow, ledger, dan auto-complete."""
import hashlib
import re
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.finance import midtrans
from app.finance.ledger import seller_summary
from app.finance.service import auto_complete_due, complete_item, deliver_item, mark_order_paid
from app.models import (
    Category, LedgerEntry, Order, OrderItem, Payment, Service, ServicePackage, User, compute_fee, utcnow,
)

SERVER_KEY = "SB-Mid-server-TESTKEY"
SNAP_URL = "https://app.sandbox.midtrans.com/snap/v4/redirection/tok"


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "MIDTRANS_SERVER_KEY": SERVER_KEY})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class FakeMidtrans:
    """Pengganti HTTP ke Midtrans: mencatat panggilan dan menjawab status sesuai skenario tes."""

    def __init__(self, monkeypatch):
        self.snap_calls, self.statuses, self.snap_url, self.fail = [], {}, SNAP_URL, False
        monkeypatch.setattr(midtrans, "_request", self)

    def __call__(self, method, url, payload=None):
        if self.fail:
            raise midtrans.MidtransError("down")
        if method == "POST":
            self.snap_calls.append(payload)
            return 201, {"token": "tok-" + payload["transaction_details"]["order_id"], "redirect_url": self.snap_url}
        order_id = url.split("/")[-2]
        if order_id not in self.statuses:
            return 200, {"status_code": "404", "status_message": "not found"}
        return 200, self.statuses[order_id]

    def set(self, order_id, transaction_status, amount, **extra):
        self.statuses[order_id] = {"order_id": order_id, "transaction_status": transaction_status,
                                   "gross_amount": f"{amount}.00", "payment_type": "qris", "status_code": "200", **extra}


@pytest.fixture
def mid(monkeypatch):
    return FakeMidtrans(monkeypatch)


def make_user(role):
    n = User.query.count() + 1
    u = User(name=f"User {n}", email=f"u{n}@example.test", phone=f"08120000{n:04d}", role=role, is_verified=True)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def login(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user.id
    return c


def csrf(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def make_order(seller, customer, status="pending", price=150000):
    cat = Category.query.first() or Category(name="Desain")
    db.session.add(cat)
    db.session.flush()
    svc = Service(seller_id=seller.id, category_id=cat.id, title="Desain logo", description="Desain logo profesional untuk brand.")
    svc.packages.append(ServicePackage(name="Basic", price=price, duration_days=3))
    db.session.add(svc)
    db.session.flush()
    pkg = svc.packages[0]
    order = Order(customer_id=customer.id, status=status, total=price)
    db.session.add(order)
    db.session.flush()
    item = OrderItem(order_id=order.id, package_id=pkg.id, seller_id=seller.id, price=price,
                     service_title=svc.title, package_name=pkg.name, duration_days=3)
    db.session.add(item)
    db.session.commit()
    return order, item


def sign(order_id, status_code, gross, key=SERVER_KEY):
    return hashlib.sha512(f"{order_id}{status_code}{gross}{key}".encode()).hexdigest()


def notify(client, order_id, gross="150000.00", status_code="200", signature=None):
    return client.post("/payments/midtrans/notification", json={
        "order_id": order_id, "status_code": status_code, "gross_amount": gross,
        "signature_key": signature or sign(order_id, status_code, gross), "transaction_status": "settlement"})


@pytest.fixture
def parties(app):
    return make_user("seller"), make_user("customer")


# ---------------------------------------------------------------- komisi
@pytest.mark.parametrize("price,bps,expected", [
    (150000, 1000, (15000, 135000)), (333, 1000, (33, 300)), (335, 1000, (34, 301)),
    (100, 0, (0, 100)), (999, 500, (50, 949)),
])
def test_compute_fee_rounds_half_up_with_integers(price, bps, expected):
    assert compute_fee(price, bps) == expected


def test_order_item_snapshots_commission_automatically(app, parties):
    order, item = make_order(*parties)
    assert (item.fee_bps, item.platform_fee, item.seller_net) == (1000, 15000, 135000)


def test_snapshot_is_frozen_when_commission_changes(app, parties):
    order, item = make_order(*parties)
    app.config["COMMISSION_BPS"] = 2000
    _, other = make_order(*parties)
    db.session.refresh(item)
    assert item.platform_fee == 15000          # order lama tidak berubah
    assert other.platform_fee == 30000          # order baru memakai komisi baru


# ---------------------------------------------------------------- memulai pembayaran
def test_pay_start_creates_snap_transaction_and_redirects(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = login(app, customer)
    r = c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")})
    assert r.status_code == 302 and r.headers["Location"] == SNAP_URL
    payload = mid.snap_calls[0]
    assert payload["transaction_details"] == {"order_id": f"NTN-{order.id}-1", "gross_amount": 150000}
    assert sum(i["price"] * i["quantity"] for i in payload["item_details"]) == 150000
    assert all(len(i["name"]) <= 50 for i in payload["item_details"])
    assert "finish" in payload["callbacks"]
    assert Payment.query.one().status == "pending"


def test_pay_start_reuses_pending_payment(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = login(app, customer)
    token = csrf(c, f"/pesanan/{order.id}/pembayaran")
    c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": token})
    c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": token})
    assert len(mid.snap_calls) == 1 and Payment.query.count() == 1


def test_pay_start_only_owner_customer(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    stranger = login(app, make_user("customer"))
    assert stranger.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(stranger, "/jasa")}).status_code == 404
    sc = login(app, seller)   # seller boleh jadi pembeli, tapi bukan pemilik order ini
    assert sc.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(sc, "/seller/pesanan")}).status_code == 404
    admin = login(app, make_user("admin"))
    assert admin.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(admin, "/admin/")}).status_code == 403


def test_pay_start_handles_midtrans_outage(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = login(app, customer)
    token = csrf(c, f"/pesanan/{order.id}/pembayaran")
    mid.fail = True
    r = c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": token}, follow_redirects=True)
    assert "tidak tersedia" in r.get_data(as_text=True)
    assert Payment.query.one().status == "failed" and Order.query.one().status == "pending"


def test_pay_start_blocks_offsite_redirect(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    mid.snap_url = "https://evil.example/phish"
    c = login(app, customer)
    r = c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_manual_proof_route_closed_when_midtrans_enabled(app, parties):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = login(app, customer)
    assert c.post(f"/pesanan/{order.id}/bukti", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")}).status_code == 404


# ---------------------------------------------------------------- webhook
def start(app, customer, order):
    c = login(app, customer)
    c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")})
    return c


def test_webhook_rejects_bad_signature_and_changes_nothing(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "settlement", 150000)
    r = notify(app.test_client(), f"NTN-{order.id}-1", signature="0" * 128)
    assert r.status_code == 403
    assert Order.query.one().status == "pending"


def test_webhook_settlement_marks_order_paid_and_starts_deadline(app, parties, mid):
    seller, customer = parties
    order, item = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "settlement", 150000)
    r = notify(app.test_client(), f"NTN-{order.id}-1")      # tanpa token CSRF: webhook dikecualikan
    assert r.status_code == 200
    db.session.refresh(order)
    db.session.refresh(item)
    assert order.status == "approved" and order.paid_at is not None
    assert item.status == "in_progress" and item.due_at is not None
    assert (item.due_at - order.paid_at.replace(tzinfo=item.due_at.tzinfo)) == timedelta(days=3)
    assert Payment.query.one().status == "paid"


def test_webhook_is_idempotent(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "settlement", 150000)
    client = app.test_client()
    notify(client, f"NTN-{order.id}-1")
    first = Order.query.one().paid_at
    notify(client, f"NTN-{order.id}-1")
    assert Order.query.one().paid_at == first and Payment.query.one().status == "paid"


def test_webhook_trusts_status_api_not_payload(app, parties, mid):
    """Payload webhook bilang settlement, tapi Status API bilang pending -> tidak boleh dianggap lunas."""
    seller, customer = parties
    order, _ = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "pending", 150000)
    notify(app.test_client(), f"NTN-{order.id}-1")
    assert Order.query.one().status == "pending"


def test_webhook_rejects_amount_mismatch(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "settlement", 1000)         # dibayar jauh lebih kecil
    notify(app.test_client(), f"NTN-{order.id}-1", gross="1000.00")
    assert Order.query.one().status == "pending"
    assert "tidak cocok" in Payment.query.one().note


def test_card_capture_needs_fraud_accept(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "capture", 150000, fraud_status="challenge")
    notify(app.test_client(), f"NTN-{order.id}-1")
    assert Order.query.one().status == "pending"
    mid.set(f"NTN-{order.id}-1", "capture", 150000, fraud_status="accept")
    notify(app.test_client(), f"NTN-{order.id}-1")
    assert Order.query.one().status == "approved"


def test_expired_payment_allows_new_attempt(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "expire", 150000)
    notify(app.test_client(), f"NTN-{order.id}-1")
    assert Payment.query.one().status == "expired" and Order.query.one().status == "pending"
    c.post(f"/pesanan/{order.id}/bayar", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")})
    assert [p.midtrans_order_id for p in Payment.query.order_by(Payment.id)] == [f"NTN-{order.id}-1", f"NTN-{order.id}-2"]


def test_duplicate_payment_is_flagged_not_double_counted(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    db.session.add(Payment(order_id=order.id, midtrans_order_id=f"NTN-{order.id}-1", gross_amount=150000))
    db.session.add(Payment(order_id=order.id, midtrans_order_id=f"NTN-{order.id}-2", gross_amount=150000))
    db.session.commit()
    for n in (1, 2):
        mid.set(f"NTN-{order.id}-{n}", "settlement", 150000)
        notify(app.test_client(), f"NTN-{order.id}-{n}")
    second = Payment.query.filter_by(midtrans_order_id=f"NTN-{order.id}-2").one()
    assert "ganda" in second.note and Order.query.one().status == "approved"


def test_webhook_unknown_order_and_outage(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    client = app.test_client()
    assert notify(client, "NTN-999-1").status_code == 200           # tidak dikenal: diabaikan, tidak di-retry
    start(app, customer, order)
    mid.fail = True
    assert notify(client, f"NTN-{order.id}-1").status_code == 502   # Midtrans akan mengirim ulang


def test_webhook_disabled_without_server_key(app, parties):
    app.config["MIDTRANS_SERVER_KEY"] = ""
    assert app.test_client().post("/payments/midtrans/notification", json={}).status_code == 404


def test_finish_page_ignores_query_params_and_asks_status_api(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "pending", 150000)
    c.get(f"/pesanan/{order.id}/pembayaran/selesai?transaction_status=settlement&status_code=200")
    assert Order.query.one().status == "pending"          # query string palsu tidak berpengaruh
    mid.set(f"NTN-{order.id}-1", "settlement", 150000)
    c.get(f"/pesanan/{order.id}/pembayaran/selesai")
    assert Order.query.one().status == "approved"


def test_check_button_syncs_payment(app, parties, mid):
    seller, customer = parties
    order, _ = make_order(seller, customer)
    c = start(app, customer, order)
    mid.set(f"NTN-{order.id}-1", "settlement", 150000)
    c.post(f"/pesanan/{order.id}/pembayaran/cek", data={"_csrf": csrf(c, f"/pesanan/{order.id}/pembayaran")})
    assert Order.query.one().status == "approved"


# ---------------------------------------------------------------- unit Midtrans
def test_signature_verification(app):
    with app.app_context():
        good = {"order_id": "A-1", "status_code": "200", "gross_amount": "150000.00",
                "signature_key": sign("A-1", "200", "150000.00")}
        assert midtrans.verify_signature(good)
        assert not midtrans.verify_signature({**good, "gross_amount": "1.00"})
        assert not midtrans.verify_signature({"order_id": "A-1"})


@pytest.mark.parametrize("data,expected", [
    ({"transaction_status": "settlement"}, "paid"),
    ({"transaction_status": "capture", "fraud_status": "accept"}, "paid"),
    ({"transaction_status": "capture", "fraud_status": "challenge"}, "pending"),
    ({"transaction_status": "pending"}, "pending"),
    ({"transaction_status": "expire"}, "expired"),
    ({"transaction_status": "deny"}, "failed"),
    ({"transaction_status": "cancel"}, "failed"),
    ({"transaction_status": "refund"}, "refunded"),
])
def test_classify(data, expected):
    assert midtrans.classify(data) == expected


# ---------------------------------------------------------------- pengerjaan + escrow
def paid_item(parties, price=150000):
    seller, customer = parties
    order, item = make_order(seller, customer, price=price)
    mark_order_paid(order)
    db.session.commit()
    return order, item


def test_full_lifecycle_releases_funds_only_after_buyer_accepts(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    assert seller_summary(seller.id)["held"] == 135000

    deliver_item(item, "Hasil final terlampir.", "https://drive.example/x")
    assert seller_summary(seller.id) == {"held": 135000, "clearing": 0, "available": 0, "income": 0, "withdrawn": 0}
    assert LedgerEntry.query.count() == 0                      # belum ada dana dirilis

    c = login(app, customer)
    r = c.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": csrf(c, f"/akun/pesanan/{order.id}")})
    assert r.status_code == 302
    db.session.refresh(item)
    assert item.status == "completed"

    earning = LedgerEntry.query.filter_by(kind="earning").one()
    commission = LedgerEntry.query.filter_by(kind="commission").one()
    assert (earning.user_id, earning.amount) == (seller.id, 135000)
    assert (commission.user_id, commission.amount) == (None, 15000)
    assert earning.amount + commission.amount == item.price      # tidak ada rupiah yang hilang

    now = utcnow()
    assert seller_summary(seller.id, now) == {"held": 0, "clearing": 135000, "available": 0, "income": 135000, "withdrawn": 0}
    later = seller_summary(seller.id, now + timedelta(days=3, minutes=1))
    assert later["available"] == 135000 and later["clearing"] == 0


def test_only_the_buyer_can_accept_and_only_once(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    deliver_item(item, "Hasil final terlampir.")
    other = login(app, make_user("customer"))
    assert other.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": csrf(other, "/jasa")}).status_code == 404
    sc = login(app, seller)   # seller bukan pembeli order ini
    assert sc.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": csrf(sc, "/seller/pesanan")}).status_code == 404

    c = login(app, customer)
    token = csrf(c, f"/akun/pesanan/{order.id}")
    c.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": token})
    c.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": token})   # klik ganda
    assert LedgerEntry.query.count() == 2


def test_buyer_cannot_accept_before_delivery(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    c = login(app, customer)
    c.post(f"/pesanan/item/{item.id}/terima", data={"_csrf": csrf(c, f"/akun/pesanan/{order.id}")})
    db.session.refresh(item)
    assert item.status == "in_progress" and LedgerEntry.query.count() == 0


def test_cannot_deliver_before_payment(app, parties):
    seller, customer = parties
    order, item = make_order(seller, customer)                 # masih pending
    with pytest.raises(Exception):
        deliver_item(item, "Terlalu cepat dikirim.")


def test_auto_complete_after_deadline(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    deliver_item(item, "Hasil final terlampir.")
    assert auto_complete_due(utcnow() + timedelta(days=2)) == 0          # belum lewat 3 hari
    assert auto_complete_due(utcnow() + timedelta(days=3, minutes=1)) == 1
    db.session.refresh(item)
    assert item.status == "completed"
    assert auto_complete_due(utcnow() + timedelta(days=30)) == 0         # idempoten
    assert LedgerEntry.query.count() == 2


def test_auto_complete_cli(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    deliver_item(item, "Hasil final terlampir.")
    item.auto_complete_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    result = app.test_cli_runner().invoke(args=["auto-complete"])
    assert "1 pesanan" in result.output
    db.session.refresh(item)
    assert item.status == "completed"


def test_ledger_unique_constraint_blocks_double_release(app, parties):
    order, item = paid_item(parties)
    deliver_item(item, "Hasil final terlampir.")
    complete_item(item, "buyer")
    db.session.add(LedgerEntry(kind="earning", user_id=item.seller_id, order_item_id=item.id, amount=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_manual_admin_approval_starts_deadline_too(app, parties):
    app.config["MIDTRANS_SERVER_KEY"] = ""
    seller, customer = parties
    order, item = make_order(seller, customer, status="waiting")
    admin = login(app, make_user("admin"))
    admin.post(f"/admin/orders/{order.id}/decision", data={"_csrf": csrf(admin, "/admin/orders"), "decision": "approve"})
    db.session.refresh(order)
    db.session.refresh(item)
    assert order.status == "approved" and order.paid_at is not None and item.due_at is not None


# ---------------------------------------------------------------- tampilan
def test_seller_finance_page_shows_summary_and_is_seller_only(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    html = login(app, seller).get("/seller/keuangan").get_data(as_text=True)
    assert "Dana ditahan" in html and "135.000" in html
    assert login(app, customer).get("/seller/keuangan").status_code == 403
    assert app.test_client().get("/seller/keuangan").status_code == 302


def test_buyer_sees_accept_button_only_when_delivered(app, parties):
    seller, customer = parties
    order, item = paid_item(parties)
    c = login(app, customer)
    assert "Terima hasil" not in c.get(f"/akun/pesanan/{order.id}").get_data(as_text=True)
    deliver_item(item, "Hasil final terlampir.", "https://drive.example/x")
    html = c.get(f"/akun/pesanan/{order.id}").get_data(as_text=True)
    assert "Terima hasil" in html and "Hasil final terlampir." in html and "dana ditahan" in html.lower()


def test_seller_orders_shows_fee_breakdown_and_deliver_form(app, parties):
    seller, customer = parties
    paid_item(parties)
    html = login(app, seller).get("/seller/pesanan").get_data(as_text=True)
    assert "Kirim hasil" in html and "135.000" in html and "15.000" in html

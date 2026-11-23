"""Tes alur pengajuan seller: syarat 100%, rekening terenkripsi, review admin, naik role."""
import logging
import re
from datetime import date

import pytest

from app import create_app
from app.crypto import decrypt_text
from app.extensions import db
from app.models import Address, Category, Experience, PayoutAccount, Project, SellerApplication, User
from app.seller_apply.logic import names_match
from tests.test_auth import client


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role="customer", **kw):
    n = User.query.count() + 1
    u = User(name=kw.pop("name", f"User {n}"), email=f"user{n}@example.com",
             phone=kw.pop("phone", f"0812345678{n:02d}"), role=role, is_verified=True, **kw)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def login(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user.id
    return c


def post(client, path, data=None, page="/akun/seller/", **kw):
    html = client.get(page).get_data(as_text=True)
    token = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    return client.post(path, data=dict(data or {}, _csrf=token), **kw)


def complete_profile(user):
    """Isi semua syarat selain rekening, kategori, deskripsi, dan pengalaman."""
    user.avatar = "uploads/avatars/x.webp"
    user.birth_date = date(2000, 1, 1)
    user.gender = "male"
    db.session.add(Address(user_id=user.id, label="Rumah", recipient_name=user.name,
                           phone=user.phone, full_address="Jl. Contoh No. 1, Batam"))
    db.session.commit()


def fill_application(client, user):
    cat = Category.query.first() or Category(name="Web Development")
    db.session.add(cat)
    db.session.commit()
    post(client, "/akun/seller/rekening", {"bank_name": "BCA", "account_number": "1234 5678 90",
                                           "account_holder": user.name.upper()})
    post(client, "/akun/seller/kategori", {"categories": [cat.id],
                                          "service_plan": "Saya membuat website company profile dan toko online untuk UMKM."})
    post(client, "/akun/seller/pengalaman", {"position": "Developer", "institution": "Freelance",
                                         "start_date": "2022-01-01", "is_current": "1"})


@pytest.fixture
def ready(app):
    """Customer yang datanya sudah lengkap 100% tapi belum mengajukan."""
    user = make_user(name="Dimas Pratama")
    complete_profile(user)
    client = login(app, user)
    fill_application(client, user)
    return user, client


# ---------------------------------------------------------------- akses
def test_guest_is_redirected_to_login(app):
    r = app.test_client().get("/akun/seller/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_admin_cannot_apply(app):
    admin = make_user("admin")
    assert login(app, admin).get("/akun/seller/").status_code == 403


def test_nav_shows_jadi_seller_only_for_customer(app):
    cust, seller = make_user(), make_user("seller")
    assert "Jadi Seller" in login(app, cust).get("/akun/").get_data(as_text=True)
    assert "Jadi Seller" not in login(app, seller).get("/akun/").get_data(as_text=True)


# ---------------------------------------------------------------- kelengkapan
def test_new_customer_starts_incomplete_and_cannot_submit(app):
    user = make_user()
    client = login(app, user)
    html = client.get("/akun/seller/").get_data(as_text=True)
    assert "Kelengkapan data" in html and "disabled" in html
    r = post(client, "/akun/seller/ajukan", follow_redirects=True)
    assert "Lengkapi semua syarat" in r.get_data(as_text=True)
    assert db.session.get(User, user.id).seller_application is None


def test_names_match_rules():
    assert names_match("Dimas Pratama", "dimas pratama")
    assert names_match("Pratama Dimas", "Dimas Pratama")          # urutan bebas
    assert names_match("Dimas Pratama", "Dimas Putra Pratama")    # nama tengah
    assert not names_match("Budi Santoso", "Dimas Pratama")
    assert not names_match("", "Dimas")


# ---------------------------------------------------------------- rekening
def test_payout_is_encrypted_and_masked(app, caplog):
    user = make_user(name="Dimas Pratama")
    client = login(app, user)
    with caplog.at_level(logging.INFO, logger="nineteen.audit"):
        post(client, "/akun/seller/rekening", {"bank_name": "BCA", "account_number": "1234-5678-90",
                                               "account_holder": "Dimas Pratama"})
    acc = PayoutAccount.query.one()
    assert acc.account_last4 == "7890" and acc.masked_number.endswith("7890")
    assert "1234567890" not in acc.account_number_enc          # tidak tersimpan polos
    assert decrypt_text(acc.account_number_enc) == "1234567890"
    assert "1234567890" not in caplog.text                     # tidak masuk log
    html = client.get("/akun/seller/").get_data(as_text=True)
    assert "7890" in html and "1234567890" not in html


@pytest.mark.parametrize("data,msg", [
    ({"bank_name": "BankPalsu", "account_number": "123456", "account_holder": "Dimas Pratama"}, "Pilih bank"),
    ({"bank_name": "BCA", "account_number": "12ab56", "account_holder": "Dimas Pratama"}, "hanya angka"),
    ({"bank_name": "BCA", "account_number": "123456", "account_holder": "Budi Santoso"}, "harus sama"),
])
def test_payout_validation(app, data, msg):
    client = login(app, make_user(name="Dimas Pratama"))
    r = post(client, "/akun/seller/rekening", data, follow_redirects=True)
    assert msg.lower() in r.get_data(as_text=True).lower()
    assert PayoutAccount.query.count() == 0


# ---------------------------------------------------------------- pengalaman & portofolio
def test_experience_and_project_crud_and_ownership(app):
    a, b = make_user(), make_user()
    ca = login(app, a)
    post(ca, "/akun/seller/pengalaman", {"position": "Dev", "institution": "PT X",
                                         "start_date": "2022-01-01", "is_current": "1"})
    post(ca, "/akun/seller/portofolio", {"title": "Web Toko", "link": "https://contoh.id"})
    exp, proj = Experience.query.one(), Project.query.one()
    cb = login(app, b)
    assert post(cb, f"/akun/seller/pengalaman/{exp.id}/hapus").status_code == 404   # bukan miliknya
    assert post(cb, f"/akun/seller/portofolio/{proj.id}/hapus").status_code == 404
    post(ca, f"/akun/seller/pengalaman/{exp.id}/hapus")
    assert Experience.query.count() == 0


def test_project_rejects_unsafe_link(app):
    client = login(app, make_user())
    r = post(client, "/akun/seller/portofolio", {"title": "Jahat", "link": "javascript:alert(1)"}, follow_redirects=True)
    assert "http://" in r.get_data(as_text=True)
    assert Project.query.count() == 0

def test_periode_pengalaman_validasi_tanggal(app):
    from app.models import Experience
    c = login(app, make_user())
    base = {"position": "Dev", "institution": "PT X"}
    post(c, "/akun/seller/pengalaman", base)
    post(c, "/akun/seller/pengalaman", dict(base, start_date="2022-05-01"))
    post(c, "/akun/seller/pengalaman", dict(base, start_date="2023-05-01", end_date="2022-01-01"))
    post(c, "/akun/seller/pengalaman", dict(base, start_date="2999-01-01", is_current="1"))
    assert Experience.query.count() == 0
    post(c, "/akun/seller/pengalaman", dict(base, start_date="2022-01-10", end_date="2024-03-05"))
    post(c, "/akun/seller/pengalaman", dict(base, start_date="2024-04-01", is_current="1"))
    labels = sorted(e.period_label for e in Experience.query.all())
    assert labels == ["Apr 2024 - Sekarang", "Jan 2022 - Mar 2024"]

# ---------------------------------------------------------------- pengajuan
def test_full_application_reaches_100_and_submits(app, ready):
    user, client = ready
    html = client.get("/akun/seller/").get_data(as_text=True)
    assert "100%" in html
    post(client, "/akun/seller/ajukan")
    application = db.session.get(User, user.id).seller_application
    assert application.status == "submitted" and application.submitted_at is not None
    assert db.session.get(User, user.id).role == "customer"       # role BELUM berubah


def test_submitted_application_is_locked(app, ready):
    user, client = ready
    post(client, "/akun/seller/ajukan")
    before = PayoutAccount.query.one().account_last4
    r = post(client, "/akun/seller/rekening", {"bank_name": "BRI", "account_number": "999999",
                                                "account_holder": "Dimas Pratama"}, follow_redirects=True)
    assert "tidak bisa diubah" in r.get_data(as_text=True)
    assert PayoutAccount.query.one().account_last4 == before
    r = post(client, "/akun/seller/pengalaman", {"position": "Developer", "institution": "Freelance",
                                                 "start_date": "2022-01-01", "is_current": "1"},
             follow_redirects=True)
    assert "tidak bisa diubah" in r.get_data(as_text=True)

    


# ---------------------------------------------------------------- admin
def submitted(app, ready):
    user, client = ready
    post(client, "/akun/seller/ajukan")
    return user, SellerApplication.query.filter_by(user_id=user.id).one()


def admin_post(app, application, data):
    admin = User.query.filter_by(role="admin").first() or make_user("admin")
    c = login(app, admin)
    return post(c, f"/admin/seller-applications/{application.id}/decision", data,
                page=f"/admin/seller-applications/{application.id}", follow_redirects=True)


def test_admin_pages_require_admin(app, ready):
    user, application = submitted(app, ready)
    assert login(app, user).get("/admin/seller-applications").status_code == 403
    assert app.test_client().get("/admin/seller-applications").status_code == 302


def test_admin_sees_queue_and_masked_payout(app, ready):
    user, application = submitted(app, ready)
    admin = login(app, make_user("admin"))
    assert "Dimas Pratama" in admin.get("/admin/seller-applications").get_data(as_text=True)
    detail = admin.get(f"/admin/seller-applications/{application.id}").get_data(as_text=True)
    assert "7890" in detail and "1234567890" not in detail
    assert "Nama pemilik cocok" in detail


def test_approve_promotes_to_seller(app, ready):
    user, application = submitted(app, ready)
    admin_post(app, application, {"decision": "approve"})
    db.session.refresh(user)
    assert user.role == "seller"
    assert user.seller_application.status == "approved"
    assert user.seller_application.reviewed_by is not None
    html = login(app, user).get("/akun/seller/").get_data(as_text=True)
    assert "sudah menjadi seller" in html


def test_reject_and_revision_require_note(app, ready):
    user, application = submitted(app, ready)
    r = admin_post(app, application, {"decision": "reject", "note": ""})
    assert "wajib diisi" in r.get_data(as_text=True)
    db.session.refresh(application)
    assert application.status == "submitted"

    admin_post(app, application, {"decision": "revision", "note": "Tambah portofolio yang lebih jelas"})
    db.session.refresh(application)
    assert application.status == "needs_revision"
    html = login(app, user).get("/akun/seller/").get_data(as_text=True)
    assert "Tambah portofolio yang lebih jelas" in html and "Ajukan ulang" in html


def test_resubmit_after_revision_then_reject(app, ready):
    user, application = submitted(app, ready)
    admin_post(app, application, {"decision": "revision", "note": "Perbaiki deskripsi jasa"})
    post(login(app, user), "/akun/seller/ajukan")
    db.session.refresh(application)
    assert application.status == "submitted" and application.admin_note is None
    admin_post(app, application, {"decision": "reject", "note": "Data tidak valid"})
    db.session.refresh(application)
    assert application.status == "rejected" and db.session.get(User, user.id).role == "customer"


def test_decision_only_for_submitted(app, ready):
    user, application = submitted(app, ready)
    admin_post(app, application, {"decision": "approve"})
    r = admin_post(app, application, {"decision": "reject", "note": "terlambat"})
    assert "Menunggu review" in r.get_data(as_text=True)
    db.session.refresh(application)
    assert application.status == "approved"


def test_approval_blocked_if_data_became_incomplete(app, ready):
    user, application = submitted(app, ready)
    user.avatar = None                      # pelamar menghapus foto setelah mengajukan
    db.session.commit()
    r = admin_post(app, application, {"decision": "approve"})
    assert "tidak lagi lengkap" in r.get_data(as_text=True)
    db.session.refresh(user)
    assert user.role == "customer"


def test_approved_seller_can_still_edit_portfolio_but_not_payout(app, ready):
    user, application = submitted(app, ready)
    admin_post(app, application, {"decision": "approve"})
    client = login(app, user)
    post(client, "/akun/seller/portofolio", {"title": "Proyek Baru"})
    assert Project.query.filter_by(title="Proyek Baru").count() == 1
    r = post(client, "/akun/seller/rekening", {"bank_name": "BRI", "account_number": "999999",
                                                "account_holder": "Dimas Pratama"}, follow_redirects=True)
    assert "tidak bisa diubah" in r.get_data(as_text=True)


def test_csrf_required(app):
    client = login(app, make_user())
    assert client.post("/akun/seller/ajukan").status_code == 400

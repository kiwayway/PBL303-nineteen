import io
import re

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.models import (
    Address, Category, Order, OrderItem, OtpCode, Review, Service, ServicePackage, User,
    WishlistItem,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    sent = {}
    monkeypatch.setattr(
        "app.auth.routes.send_otp_email",
        lambda email, code, purpose: sent.update(code=code, email=email, purpose=purpose),
    )
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    app.sent = sent
    monkeypatch.setattr("app.profile.avatar._static_dir", lambda: tmp_path)  # upload ke folder sementara
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role="customer", **kw):
    n = User.query.count() + 1
    u = User(name=kw.pop("name", f"User {n}"), email=f"user{n}@example.com",
             phone=f"0812345678{n:02d}", role=role, is_verified=True, **kw)
    u.set_password("password123")
    db.session.add(u)
    db.session.commit()
    return u


def login(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user.id
    return c


def csrf(client, path="/akun/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def post(client, path, data=None, page="/akun/", **kw):
    return client.post(path, data=dict(data or {}, _csrf=csrf(client, page)), **kw)


def catalog():
    seller = make_user("seller", name="CleanPro")
    cat = Category(name="Rumah")
    svc = Service(seller=seller, category=cat, title="Home Cleaning", description="d")
    pkg = ServicePackage(service=svc, name="Basic", price=150000, duration_days=2)
    db.session.add_all([cat, svc, pkg])
    db.session.commit()
    return seller, svc, pkg


def buy(customer, pkg, status="approved", item_status="completed"):
    o = Order(customer=customer, status=status, total=pkg.price)
    it = OrderItem(order=o, package=pkg, seller_id=pkg.service.seller_id, price=pkg.price,
                   status=item_status)
    db.session.add_all([o, it])
    db.session.commit()
    return o, it


def png_bytes(size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, (47, 128, 237)).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- akses
def test_semua_halaman_butuh_login(app):
    c = app.test_client()
    for path in ["/akun/", "/akun/profil", "/akun/pesanan", "/akun/alamat",
                 "/akun/wishlist", "/akun/ulasan", "/akun/keamanan"]:
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.location, path


def test_semua_halaman_render_dan_tanpa_menu_notifikasi(app):
    c = login(app, make_user(name="Nineteen"))
    for path in ["/akun/", "/akun/profil", "/akun/profil?edit=1", "/akun/pesanan",
                 "/akun/alamat", "/akun/alamat?baru=1", "/akun/wishlist", "/akun/ulasan",
                 "/akun/keamanan"]:
        r = c.get(path)
        assert r.status_code == 200, path
        assert b"Notifikasi" not in r.data
    html = c.get("/akun/").get_data(as_text=True)
    for label in ["Dashboard", "Informasi Pribadi", "Pesanan Saya", "Alamat Saya",
                  "Wishlist / Favorit", "Ulasan Saya", "Keamanan Akun", "Logout"]:
        assert label in html


def test_post_tanpa_csrf_ditolak(app):
    c = login(app, make_user())
    assert c.post("/akun/profil", data={"name": "x"}).status_code == 400


def test_navbar_menautkan_ke_akun(app):
    c = login(app, make_user(name="Budi"))
    html = c.get("/").get_data(as_text=True)
    assert "/akun/" in html and "Halo, <b>Budi</b>" in html


# ---------------------------------------------------------------- dashboard
def test_dashboard_hitung_dan_maks_tiga_pesanan(app):
    _, _, pkg = catalog()
    me = make_user()
    buy(me, pkg, "approved", "in_progress")
    buy(me, pkg, "approved", "completed")
    buy(me, pkg, "pending", "in_progress")
    buy(me, pkg, "approved", "completed")
    db.session.add(WishlistItem(user_id=me.id, service_id=pkg.service_id))
    db.session.commit()
    html = login(app, me).get("/akun/").get_data(as_text=True)
    assert html.count("/akun/pesanan/") == 3  # maksimal tiga pesanan terbaru
    assert "Selamat datang kembali" in html and "Jasa Favorit" in html


def test_dashboard_tidak_memuat_pesanan_orang_lain(app):
    _, _, pkg = catalog()
    me, other = make_user(), make_user()
    buy(other, pkg)
    assert b"/akun/pesanan/" not in login(app, me).get("/akun/").data


# ---------------------------------------------------------------- informasi pribadi
def test_edit_profil_berhasil(app):
    me = make_user()
    c = login(app, me)
    r = post(c, "/akun/profil", {
        "name": "  Nineteen   Studios ", "username": "Nineteen_01", "phone": "+62 812-9999-0000",
        "birth_date": "2000-08-17", "gender": "female"}, page="/akun/profil?edit=1")
    assert r.status_code == 302
    u = db.session.get(User, me.id)
    assert (u.name, u.username, u.phone, u.gender) == (
        "Nineteen Studios", "nineteen_01", "08129999000" + "0", "female")
    assert u.birth_date.isoformat() == "2000-08-17"
    assert b"berhasil disimpan" in c.get("/akun/profil").data


def test_edit_profil_validasi(app):
    me, other = make_user(), make_user(username="taken")
    c = login(app, me)
    r = post(c, "/akun/profil", {"name": "", "username": "ab", "phone": "123",
                                 "birth_date": "2999-01-01", "gender": "x"})
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    for msg in ["Nama lengkap tidak boleh kosong", "Username 3", "Format nomor telepon",
                "Tanggal lahir tidak valid", "Pilihan jenis kelamin tidak valid"]:
        assert msg in html
    r = post(c, "/akun/profil", {"name": "A", "username": "taken", "phone": other.phone})
    html = r.get_data(as_text=True)
    assert "Username sudah dipakai" in html and "sudah dipakai akun lain" in html


def test_email_tidak_bisa_diubah_lewat_profil(app):
    me = make_user()
    old = me.email
    post(login(app, me), "/akun/profil", {"name": "Baru", "phone": me.phone, "email": "x@y.com"})
    assert db.session.get(User, me.id).email == old


def test_foto_profil_ganti_dan_hapus(app):
    me = make_user()
    c = login(app, me)
    r = post(c, "/akun/profil/foto", {"avatar": (io.BytesIO(png_bytes()), "a.png")},
             content_type="multipart/form-data")
    assert r.status_code == 302
    path = db.session.get(User, me.id).avatar
    assert path.startswith("uploads/avatars/") and path.endswith(".png")
    post(c, "/akun/profil/foto/hapus")
    assert db.session.get(User, me.id).avatar is None


def test_foto_profil_menolak_file_bukan_gambar(app):
    me = make_user()
    c = login(app, me)
    post(c, "/akun/profil/foto", {"avatar": (io.BytesIO(b"<svg onload=1>"), "a.svg")},
         content_type="multipart/form-data")
    assert db.session.get(User, me.id).avatar is None


# ---------------------------------------------------------------- pesanan
def test_pesanan_filter_status_cari_dan_pagination(app):
    _, _, pkg = catalog()
    me = make_user()
    o_pending, _ = buy(me, pkg, "pending", "in_progress")
    buy(me, pkg, "approved", "in_progress")
    buy(me, pkg, "approved", "completed")
    buy(me, pkg, "rejected", "in_progress")
    c = login(app, me)
    for status, n in [("all", 4), ("pending", 1), ("processing", 1), ("done", 1), ("cancelled", 1)]:
        html = c.get(f"/akun/pesanan?status={status}").get_data(as_text=True)
        assert html.count("<article") == n, status
    assert c.get("/akun/pesanan?status=ngawur").get_data(as_text=True).count("<article") == 4
    html = c.get(f"/akun/pesanan?q=NS-{o_pending.id:05d}").get_data(as_text=True)
    assert html.count("<article") == 1
    assert c.get("/akun/pesanan?q=tidak-ada").get_data(as_text=True).count("<article") == 0
    assert c.get("/akun/pesanan?q=cleaning").get_data(as_text=True).count("<article") == 4


def test_pesanan_pagination(app):
    _, _, pkg = catalog()
    me = make_user()
    for _ in range(9):
        buy(me, pkg)
    c = login(app, me)
    assert c.get("/akun/pesanan").get_data(as_text=True).count("<article") == 8
    html = c.get("/akun/pesanan?page=2").get_data(as_text=True)
    assert html.count("<article") == 1 and "Halaman 2 dari 2" in html


def test_detail_pesanan_hanya_milik_sendiri(app):
    _, _, pkg = catalog()
    me, other = make_user(), make_user()
    mine, _ = buy(me, pkg)
    theirs, _ = buy(other, pkg)
    c = login(app, me)
    r = c.get(f"/akun/pesanan/{mine.id}")
    assert r.status_code == 200 and b"Rincian harga" in r.data and b"Rp150.000" in r.data
    assert c.get(f"/akun/pesanan/{theirs.id}").status_code == 404


# ---------------------------------------------------------------- alamat
ADDR = {"label": "Rumah", "recipient_name": "Budi", "phone": "0812-3456-7890",
        "full_address": "Jl. Raja Ali Haji No. 19, Batam Centre", "notes": "pagar biru"}


def test_alamat_pertama_otomatis_utama_dan_ganti_utama(app):
    me = make_user()
    c = login(app, me)
    assert post(c, "/akun/alamat", ADDR, page="/akun/alamat?baru=1").status_code == 302
    post(c, "/akun/alamat", dict(ADDR, label="Kantor"), page="/akun/alamat?baru=1")
    a1, a2 = Address.query.order_by(Address.id).all()
    assert a1.is_primary and not a2.is_primary and a1.phone == "081234567890"
    post(c, f"/akun/alamat/{a2.id}/utama")
    db.session.expire_all()
    assert not db.session.get(Address, a1.id).is_primary and db.session.get(Address, a2.id).is_primary
    assert Address.query.filter_by(is_primary=True).count() == 1


def test_alamat_validasi_edit_dan_hapus(app):
    me = make_user()
    c = login(app, me)
    r = post(c, "/akun/alamat", dict(ADDR, full_address="pendek", phone="1"), page="/akun/alamat?baru=1")
    assert r.status_code == 400 and Address.query.count() == 0
    post(c, "/akun/alamat", ADDR, page="/akun/alamat?baru=1")
    post(c, "/akun/alamat", dict(ADDR, label="Kantor"), page="/akun/alamat?baru=1")
    primary = Address.query.filter_by(is_primary=True).one()
    post(c, f"/akun/alamat/{primary.id}", dict(ADDR, recipient_name="Siti"))
    assert db.session.get(Address, primary.id).recipient_name == "Siti"
    r = post(c, f"/akun/alamat/{primary.id}/hapus")
    assert r.status_code == 302 and Address.query.count() == 1
    html = c.get("/akun/alamat").get_data(as_text=True)
    assert "Pilih alamat utama" in html  # alamat utama dihapus -> harus pilih baru


def test_alamat_milik_orang_lain_tidak_bisa_diakses(app):
    me, other = make_user(), make_user()
    a = Address(user_id=other.id, label="Rumah", recipient_name="X", phone="081234567890",
                full_address="Jl. Rahasia No. 1 Batam", is_primary=True)
    db.session.add(a)
    db.session.commit()
    c = login(app, me)
    assert c.get(f"/akun/alamat?edit={a.id}").status_code == 404
    assert post(c, f"/akun/alamat/{a.id}", ADDR).status_code == 404
    assert post(c, f"/akun/alamat/{a.id}/hapus").status_code == 404
    assert post(c, f"/akun/alamat/{a.id}/utama").status_code == 404
    assert Address.query.count() == 1


# ---------------------------------------------------------------- wishlist
def test_wishlist_tambah_hapus_dan_layanan_tidak_tersedia(app):
    _, svc, _ = catalog()
    me = make_user()
    c = login(app, me)
    post(c, f"/akun/wishlist/{svc.id}/tambah")
    post(c, f"/akun/wishlist/{svc.id}/tambah")  # tidak ganda
    assert WishlistItem.query.count() == 1
    html = c.get("/akun/wishlist").get_data(as_text=True)
    assert "Home Cleaning" in html and "Lihat Layanan" in html
    svc.is_active = False
    db.session.commit()
    html = c.get("/akun/wishlist").get_data(as_text=True)
    assert "Layanan tidak tersedia" in html and "Lihat Layanan" not in html
    post(c, f"/akun/wishlist/{svc.id}/hapus")
    assert WishlistItem.query.count() == 0
    assert "Wishlist masih kosong" in c.get("/akun/wishlist").get_data(as_text=True)
    assert post(c, f"/akun/wishlist/{svc.id}/tambah").status_code == 404  # jasa nonaktif


def test_wishlist_redirect_next_hanya_internal(app):
    _, svc, _ = catalog()
    c = login(app, make_user())
    r = post(c, f"/akun/wishlist/{svc.id}/tambah", {"next": "https://evil.example"})
    assert r.location.endswith("/akun/wishlist")


# ---------------------------------------------------------------- ulasan
def test_ulasan_buat_edit_hapus(app):
    _, _, pkg = catalog()
    me = make_user()
    _, item = buy(me, pkg)
    c = login(app, me)
    assert b"Menunggu ulasan" in c.get("/akun/ulasan").data
    assert post(c, f"/akun/ulasan/{item.id}", {"rating": "9"}).status_code == 400
    assert post(c, f"/akun/ulasan/{item.id}", {"rating": "5", "comment": "Bagus"}).status_code == 302
    post(c, f"/akun/ulasan/{item.id}", {"rating": "4", "comment": "Cukup"})  # edit, bukan ganda
    assert Review.query.count() == 1 and Review.query.one().rating == 4
    assert b"Cukup" in c.get("/akun/ulasan").data
    assert c.get(f"/akun/ulasan?item={item.id}").status_code == 200
    post(c, f"/akun/ulasan/{item.id}/hapus")
    assert Review.query.count() == 0


def test_ulasan_hanya_untuk_pesanan_selesai_milik_sendiri(app):
    _, _, pkg = catalog()
    me, other = make_user(), make_user()
    _, running = buy(me, pkg, "approved", "in_progress")
    _, unpaid = buy(me, pkg, "pending", "completed")
    _, theirs = buy(other, pkg)
    c = login(app, me)
    assert post(c, f"/akun/ulasan/{running.id}", {"rating": "5"}).status_code == 403
    assert post(c, f"/akun/ulasan/{unpaid.id}", {"rating": "5"}).status_code == 403
    assert post(c, f"/akun/ulasan/{theirs.id}", {"rating": "5"}).status_code == 404
    assert post(c, f"/akun/ulasan/{theirs.id}/hapus").status_code == 404
    assert Review.query.count() == 0


# ---------------------------------------------------------------- keamanan: ganti password via OTP
def test_ganti_password_wajib_otp_end_to_end(app):
    me = make_user()
    c = login(app, me)
    # tanpa OTP, halaman password baru menolak
    assert c.get("/reset-password").status_code == 302
    r = post(c, "/akun/keamanan/ganti-password", page="/akun/keamanan")
    assert r.status_code == 302 and r.location.endswith("/verify")
    assert app.sent["purpose"] == "reset" and app.sent["email"] == me.email
    assert c.get("/reset-password").status_code == 302  # OTP belum diverifikasi
    # kode salah
    assert post(c, "/verify", {"code": "000000" if app.sent["code"] != "000000" else "111111"},
                page="/verify").status_code == 400
    # kode benar -> boleh ganti password
    r = post(c, "/verify", {"code": app.sent["code"]}, page="/verify")
    assert r.status_code == 302 and r.location.endswith("/reset-password")
    r = post(c, "/reset-password", {"password": "kataSandiBaru1", "password2": "kataSandiBaru1"},
             page="/reset-password")
    assert r.status_code == 302 and r.location.endswith("/login")
    db.session.expire_all()
    u = db.session.get(User, me.id)
    assert u.check_password("kataSandiBaru1") and not u.check_password("password123")


def test_halaman_verify_dari_profil_kembali_ke_keamanan(app):
    c = login(app, make_user())
    post(c, "/akun/keamanan/ganti-password", page="/akun/keamanan")
    assert "/akun/keamanan" in c.get("/verify").get_data(as_text=True)


def test_ganti_password_tidak_spam_otp_dalam_cooldown(app):
    me = make_user()
    c = login(app, me)
    post(c, "/akun/keamanan/ganti-password", page="/akun/keamanan")
    first = app.sent["code"]
    post(c, "/akun/keamanan/ganti-password", page="/akun/keamanan")
    assert app.sent["code"] == first
    assert OtpCode.query.filter_by(user_id=me.id).count() == 1


def test_password_tidak_pernah_tampil_di_halaman_keamanan(app):
    me = make_user()
    html = login(app, me).get("/akun/keamanan").get_data(as_text=True)
    assert me.password_hash not in html and "password123" not in html
    assert me.email in html

"""Aturan bisnis pengajuan seller: syarat kelengkapan (checklist) dan pencocokan nama.

Dipisah dari routes supaya bisa dipakai di dua tempat: halaman user (/akun/seller)
dan halaman review admin (/admin/seller-applications).
"""
import re

from datetime import date
from flask import url_for

BANKS = (
    "BCA", "BRI", "BNI", "Mandiri", "BSI", "CIMB Niaga", "Permata", "Danamon",
    "BTN", "OCBC", "Jago", "SeaBank", "Neo Commerce", "Jenius (BTPN)",
)
MIN_PLAN_CHARS = 30
MIN_SELLER_AGE = 18
EDITABLE_STATUSES = ("draft", "needs_revision", "rejected")


def name_tokens(value):
    return set(re.sub(r"[^a-z\s]", " ", (value or "").lower()).split())


def names_match(a, b):
    """Nama pemilik rekening harus sama dengan nama akun.

    Cocok bila semua kata dari salah satu nama ada di nama lainnya, jadi urutan kata
    dan nama tengah tidak jadi masalah ("Dimas Pratama" cocok dengan "Dimas Putra Pratama").
    """
    ta, tb = name_tokens(a), name_tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def checklist(user, application, payout):
    """Daftar syarat jadi seller. Tiap item: key, label, ok, hint, href."""
    info = url_for("profile.info")
    plan = ((application.service_plan if application else "") or "").strip()
    payout_hint = "Isi bank, nomor rekening, dan nama pemilik."
    if payout and not names_match(payout.account_holder, user.name):
        payout_hint = "Nama pemilik rekening harus sama dengan nama di akunmu."
    items = [
        ("avatar", "Foto profil", bool(user.avatar), "Unggah foto profil.", info),
        ("name", "Nama lengkap", len((user.name or "").strip()) >= 3, "Isi nama lengkap sesuai identitas.", info),
        ("email", "Email terverifikasi", bool(user.email and user.is_verified), "Verifikasi email lewat OTP.", info),
        ("phone", "Nomor telepon", bool(user.phone), "Isi nomor telepon aktif.", info),
        ("birth", "Tanggal lahir (Minimal 18 tahun)", bool(user.birth_date), "Isi tanggal lahir. Minimal harus berusia 18 tahun.", info),
        ("gender", "Jenis kelamin", bool(user.gender), "Pilih jenis kelamin.", info),
                ("address", "Alamat", len(user.addresses) >= 1, "Tambahkan minimal satu alamat.", info + "#alamat"),
        ("payout", "Rekening pencairan",
         bool(payout and names_match(payout.account_holder, user.name)), payout_hint, "#rekening"),
        ("categories", "Kategori jasa yang ditawarkan",
         bool(application and application.categories), "Pilih minimal satu kategori.", "#kategori"),
        ("plan", "Deskripsi jasa yang akan ditawarkan", len(plan) >= MIN_PLAN_CHARS,
         f"Tulis minimal {MIN_PLAN_CHARS} karakter.", "#kategori"),
        ("track", "Pengalaman atau portofolio", len(user.experiences) + len(user.projects) >= 1,
         "Tambahkan minimal satu pengalaman atau proyek.", "#pengalaman"),
    ]
    return [dict(key=k, label=l, ok=ok, hint=h, href=href) for k, l, ok, h, href in items]


def progress(items):
    done = sum(1 for i in items if i["ok"])
    return done, len(items), round(100 * done / len(items))

def age_on(birth, today=None):
    """Umur penuh (tahun) pada tanggal `today`."""
    today = today or date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def is_min_age(birth, years=MIN_SELLER_AGE):
    """True bila sudah genap `years` tahun (ulang tahun ke-18 sudah lewat atau hari ini)."""
    return age_on(birth) >= years
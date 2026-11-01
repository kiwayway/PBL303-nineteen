import re
import secrets
from functools import wraps

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..mailer import send_otp_email
from ..models import OtpCode, User, utcnow

bp = Blueprint("auth", __name__)

MAX_OTP_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- CSRF sederhana (tanpa dependensi tambahan) ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@bp.before_app_request
def _check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Token CSRF tidak valid. Muat ulang halaman lalu coba lagi.")


# ---------- helper ----------
def normalize_phone(raw):
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+62"):
        digits = "0" + digits[3:]
    elif digits.startswith("62"):
        digits = "0" + digits[2:]
    return digits


def current_user():
    uid = session.get("user_id")
    user = db.session.get(User, uid) if uid else None
    return user if user and user.is_active else None


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("auth.login"))
        return view(*a, **kw)
    return wrapped


def issue_otp(user, purpose):
    """Buat OTP baru (membatalkan yang lama), kirim lewat email."""
    OtpCode.query.filter_by(user_id=user.id, purpose=purpose, used=False).update({"used": True})
    code = f"{secrets.randbelow(10**6):06d}"
    db.session.add(OtpCode(
        user_id=user.id, purpose=purpose,
        code_hash=generate_password_hash(code), expires_at=OtpCode.new_expiry(),
    ))
    db.session.commit()
    send_otp_email(user.email, code, purpose)


def latest_otp(user_id, purpose):
    return (OtpCode.query.filter_by(user_id=user_id, purpose=purpose, used=False)
            .order_by(OtpCode.id.desc()).first())


def check_otp(user_id, purpose, code):
    """Return (ok, pesan_error)."""
    otp = latest_otp(user_id, purpose)
    if not otp:
        return False, "Kode tidak ditemukan. Silakan kirim ulang."
    if otp.is_expired():
        return False, "Kode sudah kedaluwarsa. Silakan kirim ulang."
    if otp.attempts >= MAX_OTP_ATTEMPTS:
        return False, "Terlalu banyak percobaan. Silakan kirim ulang kode."
    if not check_password_hash(otp.code_hash, code):
        otp.attempts += 1
        db.session.commit()
        return False, "Kode salah."
    otp.used = True
    db.session.commit()
    return True, None


def seconds_since_last_otp(user_id, purpose):
    last = (OtpCode.query.filter_by(user_id=user_id, purpose=purpose)
            .order_by(OtpCode.id.desc()).first())
    if not last:
        return 10**6
    created = last.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=utcnow().tzinfo)
    return (utcnow() - created).total_seconds()


# ---------- register ----------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = normalize_phone(request.form.get("phone"))
        password = request.form.get("password", "")

        error = None
        if not name:
            error = "Nama lengkap wajib diisi."
        elif not EMAIL_RE.match(email):
            error = "Format email tidak valid."
        elif not re.fullmatch(r"0\d{8,13}", phone):
            error = "Nomor HP tidak valid."
        elif len(password) < 8:
            error = "Password minimal 8 karakter."
        if error:
            flash(error, "error")
            return render_template("auth/register.html", form=request.form), 400

        existing = User.query.filter(or_(User.email == email, User.phone == phone)).first()
        if existing and existing.is_verified:
            flash("Email atau nomor HP sudah terdaftar.", "error")
            return render_template("auth/register.html", form=request.form), 409
        if existing:  # akun lama belum terverifikasi -> perbarui data
            clash = User.query.filter(
                or_(User.email == email, User.phone == phone), User.id != existing.id
            ).first()
            if clash:
                flash("Email atau nomor HP sudah terdaftar.", "error")
                return render_template("auth/register.html", form=request.form), 409
            user = existing
            user.name, user.email, user.phone = name, email, phone
        else:
            user = User(name=name, email=email, phone=phone)
            db.session.add(user)
        user.set_password(password)
        db.session.commit()

        issue_otp(user, OtpCode.PURPOSE_VERIFY)
        session["pending_user_id"] = user.id
        session["pending_purpose"] = OtpCode.PURPOSE_VERIFY
        return redirect(url_for("auth.verify"))
    return render_template("auth/register.html", form={})


# ---------- verify (dipakai untuk verifikasi email & reset password) ----------
@bp.route("/verify", methods=["GET", "POST"])
def verify():
    uid, purpose = session.get("pending_user_id"), session.get("pending_purpose")
    user = db.session.get(User, uid) if uid else None
    ghost = user is None and purpose == OtpCode.PURPOSE_RESET and session.get("pending_email")
    if not (user or ghost) or purpose not in (OtpCode.PURPOSE_VERIFY, OtpCode.PURPOSE_RESET):
        return redirect(url_for("auth.login"))
    email = user.email if user else session["pending_email"]

    back = url_for("auth.register" if purpose == OtpCode.PURPOSE_VERIFY else "auth.forgot_password")
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        if len(code) != 6:
            flash("Masukkan 6 digit kode.", "error")
            return render_template("auth/verify.html", email=email, back=back), 400
        ok, err = check_otp(user.id, purpose, code) if user else (False, "Kode salah.")
        if not ok:
            flash(err, "error")
            return render_template("auth/verify.html", email=email, back=back), 400

        session.pop("pending_user_id", None)
        session.pop("pending_purpose", None)
        if purpose == OtpCode.PURPOSE_VERIFY:
            user.is_verified = True
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            flash("Email berhasil diverifikasi. Selamat datang!", "success")
            return redirect(url_for("main.index"))
        session["reset_user_id"] = user.id  # izin ganti password
        return redirect(url_for("auth.reset_password"))
    return render_template("auth/verify.html", email=email, back=back)


@bp.post("/verify/resend")
def resend():
    uid, purpose = session.get("pending_user_id"), session.get("pending_purpose")
    user = db.session.get(User, uid) if uid else None
    if not user:  # sesi tidak valid atau email tak terdaftar: tampilkan respons yang sama
        flash("Kode baru sudah dikirim.", "success") if session.get("pending_email") else None
        return redirect(url_for("auth.verify" if session.get("pending_email") else "auth.login"))
    if seconds_since_last_otp(user.id, purpose) < RESEND_COOLDOWN_SECONDS:
        flash("Tunggu sebentar sebelum meminta kode baru.", "error")
    else:
        issue_otp(user, purpose)
        flash("Kode baru sudah dikirim.", "success")
    return redirect(url_for("auth.verify"))


# ---------- login / logout ----------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("main.index"))
    if request.method == "POST":
        ident = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        if "@" in ident:
            user = User.query.filter_by(email=ident.lower()).first()
        else:
            user = User.query.filter_by(phone=normalize_phone(ident)).first()

        if not user or not user.check_password(password):
            flash("Email/nomor HP atau password salah.", "error")
            return render_template("auth/login.html", form=request.form), 401

        if not user.is_active:
            flash("Akun dinonaktifkan. Hubungi admin.", "error")
            return render_template("auth/login.html", form=request.form), 403

        if not user.is_verified:
            issue_otp(user, OtpCode.PURPOSE_VERIFY)
            session["pending_user_id"] = user.id
            session["pending_purpose"] = OtpCode.PURPOSE_VERIFY
            flash("Akun belum diverifikasi. Kode baru sudah dikirim ke email kamu.", "error")
            return redirect(url_for("auth.verify"))

        remember = bool(request.form.get("remember"))
        session.clear()
        session["user_id"] = user.id
        session.permanent = remember
        return redirect(url_for("main.index"))
    return render_template("auth/login.html", form={})


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------- lupa & reset password ----------
@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, is_verified=True).first()
        session.pop("pending_user_id", None)
        session["pending_purpose"] = OtpCode.PURPOSE_RESET
        session["pending_email"] = email
        if user:
            session["pending_user_id"] = user.id
            if seconds_since_last_otp(user.id, OtpCode.PURPOSE_RESET) >= RESEND_COOLDOWN_SECONDS:
                issue_otp(user, OtpCode.PURPOSE_RESET)
        # Alur dan pesan sama baik email terdaftar maupun tidak (cegah enumerasi akun).
        return redirect(url_for("auth.verify"))
    return render_template("auth/forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    user = db.session.get(User, session.get("reset_user_id") or 0)
    if not user:
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < 8:
            flash("Password minimal 8 karakter.", "error")
        elif pw != pw2:
            flash("Konfirmasi password tidak sama.", "error")
        else:
            user.set_password(pw)
            db.session.commit()
            session.clear()
            flash("Password berhasil diubah. Silakan masuk.", "success")
            return redirect(url_for("auth.login"))
        return render_template("auth/reset_password.html"), 400
    return render_template("auth/reset_password.html")

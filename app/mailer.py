import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from flask import current_app

log = logging.getLogger(__name__)


def send_otp_email(to_email, code, purpose):
    """Kirim kode OTP melalui SMTP."""

    if purpose == "verify":
        subject = "Kode Verifikasi Akun — Nineteen"
        intro = (
            "Terima kasih telah mendaftar di Nineteen!\n"
            "Gunakan kode berikut untuk memverifikasi akun kamu."
        )
    else:
        subject = "Reset Password — Nineteen"
        intro = (
            "Kami menerima permintaan untuk mereset password akun Nineteen kamu.\n"
            "Gunakan kode berikut untuk melanjutkan proses reset password."
        )

    body = (
        f"Halo!\n\n"
        f"{intro}\n\n"
        f"Kode OTP kamu: {code}\n\n"
        "Kode ini berlaku selama 10 menit.\n"
        "Jangan bagikan kode ini kepada siapa pun.\n\n"
        "Jika kamu tidak melakukan permintaan ini, abaikan email ini.\n\n"
        "Salam hangat,\n"
        "Tim Nineteen"
    )

    cfg = current_app.config

    if not cfg.get("MAIL_SERVER"):
        log.warning(
            "[DEV] MAIL_SERVER kosong. Kode %s untuk %s: %s",
            purpose,
            to_email,
            code,
        )
        return

    sender_email = cfg.get("MAIL_USERNAME")
    if not sender_email:
        log.error("MAIL_USERNAME belum dikonfigurasi.")
        raise RuntimeError("Konfigurasi email pengirim belum lengkap.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Nineteen", sender_email))
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(
            cfg["MAIL_SERVER"],
            cfg.get("MAIL_PORT", 587),
            timeout=20,
        ) as server:
            server.ehlo()

            if cfg.get("MAIL_USE_TLS", True):
                server.starttls()
                server.ehlo()

            server.login(
                sender_email,
                cfg["MAIL_PASSWORD"],
            )

            server.send_message(msg)

    except Exception:
        log.exception("Gagal mengirim email OTP.")
        raise
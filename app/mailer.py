import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger(__name__)


def send_otp_email(to_email, code, purpose):
    """Kirim kode OTP. Jika MAIL_SERVER kosong (dev), kode dicetak ke log."""
    subject = "Kode verifikasi Fasco" if purpose == "verify" else "Kode reset password Fasco"
    body = (
        f"Kode kamu: {code}\n\n"
        "Kode berlaku 10 menit. Jangan bagikan kode ini kepada siapa pun."
    )
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER"):
        log.warning("[DEV] MAIL_SERVER kosong. Kode %s untuk %s: %s", purpose, to_email, code)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=10) as s:
            if cfg["MAIL_USE_TLS"]:
                s.starttls()
            if cfg.get("MAIL_USERNAME"):
                s.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            s.send_message(msg)
    except Exception:
        log.exception("Gagal mengirim email ke %s", to_email)

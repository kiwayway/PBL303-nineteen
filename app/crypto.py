"""Enkripsi data sensitif di database (mis. nomor rekening) dengan Fernet (AES-128-CBC + HMAC).

Kunci diambil dari DATA_ENCRYPTION_KEY. Kalau kosong (dev/test), kunci diturunkan dari SECRET_KEY.
Produksi: set DATA_ENCRYPTION_KEY sendiri dan simpan cadangannya, sebab data lama tidak bisa
dibaca bila kunci hilang atau berubah. Buat kunci baru dengan:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import base64
import hashlib

from cryptography.fernet import Fernet
from flask import current_app


def _fernet():
    key = current_app.config.get("DATA_ENCRYPTION_KEY")
    if not key:
        seed = ("nineteen-data-key:" + current_app.config["SECRET_KEY"]).encode()
        key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def encrypt_text(plain):
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_text(token):
    """Raise cryptography.fernet.InvalidToken bila kunci salah atau data rusak."""
    return _fernet().decrypt(token.encode()).decode()

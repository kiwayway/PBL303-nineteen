
"""Log keamanan berformat key="value" supaya mudah di-decode Wazuh.

Contoh baris:
  event="login_failed" ip="10.19.10.5" ident="budi@mail.com" reason="bad_password"
Jangan pernah mencatat password, kode OTP, atau token.
"""
import json
import logging

_log = logging.getLogger("nineteen.audit")


def audit(event, **fields):
    parts = [f"event={json.dumps(event)}"]

    for key, value in fields.items():
        # Escape newline dan karakter "=" agar nilai input tidak
        # dapat menyamar sebagai field audit baru.
        encoded = json.dumps(
            "" if value is None else str(value)
        ).replace("=", r"\u003d")

        parts.append(f"{key}={encoded}")

    _log.info(" ".join(parts))
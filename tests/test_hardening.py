import logging
import re

import pytest

from app import create_app
from tests.test_auth import REG, client, post, register_and_verify  # noqa: F401


def test_secret_key_wajib_di_luar_test(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


@pytest.mark.parametrize("weak", ["dev-only-change-me", "isi-string-acak-panjang", "pendek"])
def test_secret_key_lemah_ditolak(monkeypatch, weak):
    monkeypatch.setenv("SECRET_KEY", weak)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_database_url_wajib_di_luar_test(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()


def test_config_valid_berjalan(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert create_app().config["SECRET_KEY"] == "x" * 40


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def bad_login(client, ident="budi@mail.com"):
    return post(client, "/login", {"identifier": ident, "password": "salah-banget"}, page="/login")


def test_login_diblokir_setelah_5_gagal(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    for _ in range(5):
        assert bad_login(client).status_code == 401
    assert bad_login(client).status_code == 429
    # password benar pun ditolak selama diblokir
    r = post(client, "/login", {"identifier": "budi@mail.com", "password": "rahasia123"}, page="/login")
    assert r.status_code == 429


def test_login_sukses_mereset_hitungan(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    for _ in range(4):
        bad_login(client)
    r = post(client, "/login", {"identifier": "budi@mail.com", "password": "rahasia123"}, page="/login")
    assert r.status_code == 302
    post(client, "/logout", {}, page="/")
    for _ in range(4):
        assert bad_login(client).status_code == 401


def test_pemblokiran_per_akun_tidak_kena_akun_lain(client):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    for _ in range(6):
        bad_login(client)
    assert bad_login(client, "orang-lain@mail.com").status_code == 401


def test_audit_log_tidak_memuat_password(client, caplog):
    register_and_verify(client)
    post(client, "/logout", {}, page="/")
    with caplog.at_level(logging.INFO, logger="fasco.audit"):
        # propagate dimatikan di app; pasang handler caplog langsung
        logging.getLogger("fasco.audit").addHandler(caplog.handler)
        try:
            bad_login(client)
        finally:
            logging.getLogger("fasco.audit").removeHandler(caplog.handler)
    text = caplog.text
    assert 'event="login_failed"' in text and 'ident="budi@mail.com"' in text
    assert "salah-banget" not in text


def test_audit_log_aman_dari_log_injection(client, caplog):
    logger = logging.getLogger("fasco.audit")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="fasco.audit"):
            post(client, "/login", {"identifier": "a@b.c\nevent=\"login_success\"", "password": "x"},
                 page="/login")
    finally:
        logger.removeHandler(caplog.handler)
    assert len([ln for ln in caplog.text.splitlines() if "event=" in ln]) == 1

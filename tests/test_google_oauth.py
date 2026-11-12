import base64
import hashlib
import hmac
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
from app.auth import google as g
from app.extensions import db
from app.models import OAuthIdentity, User

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "super-secret-value-do-not-log"
AUTH_CODE = "4/0AX4-fake-authorization-code"
FAKE_ID_TOKEN_MARK = "fake-id-token-mark"


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


GOOGLE_KEY = _new_key()    # stand-in untuk kunci privat Google
ATTACKER_KEY = _new_key()  # kunci lain: tanda tangan darinya harus ditolak
KID = "test-kid"
ORIGINAL_JWK_CLIENT = g.get_jwk_client


def _pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())


def _public_pem(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def make_claims(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Sari@Gmail.com",
        "email_verified": True,
        "name": "Sari Dewi",
        "iat": int(time.time()) - 5,
        "exp": time.time() + 3600,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def make_id_token(key=None, kid=KID, **overrides):
    """ID token RS256 yang ditandatangani `key` (default: kunci 'Google' uji)."""
    return jwt.encode(make_claims(**overrides), _pem(key or GOOGLE_KEY),
                      algorithm="RS256", headers={"kid": kid} if kid else None)


def _b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJwkClient:
    """Meniru PyJWKClient: hanya kid 'test-kid' yang dikenal, kuncinya kunci publik uji."""

    def __init__(self, state):
        self.state = state

    def get_signing_key_from_jwt(self, token):
        if self.state.get("jwks_error"):
            raise jwt.PyJWKClientError("tidak bisa mengambil JWKS")
        header = jwt.get_unverified_header(token)  # DecodeError bila bukan JWT
        if header.get("kid") != KID:
            raise jwt.PyJWKClientError("kid tidak dikenal")
        return FakeSigningKey(GOOGLE_KEY.public_key())


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class AuditCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def ctx(monkeypatch):
    """Client + pengendali respons Google palsu. Tidak ada panggilan jaringan nyata."""
    app = create_app({
        "TESTING": True,
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
        "GOOGLE_REDIRECT_URI": "http://localhost/auth/google/callback",
    })
    state = {"calls": [], "response": None}

    def fake_post(url, data=None, timeout=None, **kw):
        state["calls"].append({"url": url, "data": data, "timeout": timeout})
        resp = state["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(g.requests, "post", fake_post)
    monkeypatch.setattr(g, "get_jwk_client", lambda: FakeJwkClient(state))
    handler = AuditCapture()
    audit_log = logging.getLogger("nineteen.audit")
    audit_log.addHandler(handler)

    client = app.test_client()
    client.app = app
    client.g = state
    client.logs = handler.lines
    yield client
    audit_log.removeHandler(handler)


def start(client):
    """Mulai alur; kembalikan (state, nonce, query_params_ke_google)."""
    r = client.get("/auth/google")
    assert r.status_code == 302
    assert r.location.startswith(g.AUTH_URL)
    q = {k: v[0] for k, v in parse_qs(urlparse(r.location).query).items()}
    with client.session_transaction() as s:
        nonce = s["g_nonce"]
    return q["state"], nonce, q


def finish(client, id_token_overrides=None, state=None, code=AUTH_CODE, token_response="ok",
           extra_query=None):
    """Mulai alur lalu panggil callback seperti Google melakukannya."""
    real_state, nonce, _ = start(client)
    overrides = dict(nonce=nonce)
    overrides.update(id_token_overrides or {})
    if token_response == "ok":
        client.g["response"] = FakeResponse(200, {"id_token": make_id_token(**overrides)})
    else:
        client.g["response"] = token_response
    query = {"state": state if state is not None else real_state}
    if code is not None:
        query["code"] = code
    query.update(extra_query or {})
    return client.get("/auth/google/callback", query_string=query)


def logged_in_user_id(client):
    with client.session_transaction() as s:
        return s.get("user_id")


def add_local_user(client, **kw):
    fields = dict(name="Sari Lokal", email="sari@gmail.com", phone="081200000001",
                  is_verified=True)
    fields.update(kw)
    with client.app.app_context():
        u = User(**{k: v for k, v in fields.items() if k != "password"})
        u.set_password(kw.get("password", "passwordlokal1"))
        db.session.add(u)
        db.session.commit()
        return u.id


# ---------------------------------------------------------------- tombol & start
def test_buttons_active_when_configured(ctx):
    for page in ("/login", "/register"):
        html = ctx.get(page).get_data(as_text=True)
        assert 'href="/auth/google"' in html
        assert "segera hadir" not in html


def test_buttons_disabled_when_not_configured():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_REDIRECT_URI": "",
    })
    c = app.test_client()
    html = c.get("/login").get_data(as_text=True)
    assert 'href="/auth/google"' not in html and "belum tersedia" in html
    r = c.get("/auth/google")
    assert r.status_code == 302 and r.location.endswith("/login")


def test_start_builds_correct_authorization_request(ctx):
    state, nonce, q = start(ctx)
    assert q["client_id"] == CLIENT_ID
    assert q["response_type"] == "code"
    assert q["redirect_uri"] == "http://localhost/auth/google/callback"
    assert set(q["scope"].split()) == {"openid", "email", "profile"}
    assert q["state"] == state and q["nonce"] == nonce
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert "client_secret" not in q and CLIENT_SECRET not in ctx.get("/auth/google").location


def test_start_redirects_home_if_already_logged_in(ctx):
    add_local_user(ctx)
    with ctx.session_transaction() as s:
        s["user_id"] = 1
    r = ctx.get("/auth/google")
    assert r.status_code == 302 and urlparse(r.location).path == "/"


# ---------------------------------------------------------------- akun baru
def test_new_google_user_is_created_and_logged_in(ctx):
    r = finish(ctx)
    assert r.status_code == 302 and urlparse(r.location).path == "/"
    with ctx.app.app_context():
        u = User.query.one()
        assert (u.email, u.name) == ("sari@gmail.com", "Sari Dewi")
        assert u.role == "customer" and u.is_verified and u.is_active
        assert u.phone is None
        ident = OAuthIdentity.query.one()
        assert (ident.provider, ident.subject, ident.user_id) == ("google", "1234567890", u.id)
        uid = u.id
    assert logged_in_user_id(ctx) == uid
    assert b"Sari Dewi" in ctx.get("/").data


def test_google_user_cannot_login_with_any_password(ctx):
    finish(ctx)
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    for pw in ("", "password", "sari@gmail.com", "1234567890"):
        r = _post_login(ctx, "sari@gmail.com", pw)
        assert r.status_code == 401


def test_role_cannot_be_escalated_via_request(ctx):
    finish(ctx, extra_query={"role": "admin", "is_admin": "1"})
    with ctx.app.app_context():
        assert User.query.one().role == "customer"


def test_two_google_users_without_phone_do_not_collide(ctx):
    finish(ctx)
    other = ctx.app.test_client()
    other.g, other.app, other.logs = ctx.g, ctx.app, ctx.logs
    finish(other, {"sub": "999", "email": "lain@gmail.com", "name": "Lain"})
    with ctx.app.app_context():
        assert User.query.count() == 2 and OAuthIdentity.query.count() == 2


def test_name_falls_back_to_email_local_part(ctx):
    finish(ctx, {"name": None, "email": "tanpa.nama@gmail.com"})
    with ctx.app.app_context():
        assert User.query.one().name == "tanpa.nama"


# ---------------------------------------------------------------- akun yang sudah ada
def test_returning_google_user_reuses_account_even_if_email_changes(ctx):
    finish(ctx)
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    finish(ctx, {"email": "email.baru@gmail.com"})
    with ctx.app.app_context():
        assert User.query.count() == 1 and OAuthIdentity.query.count() == 1
        assert User.query.one().email == "sari@gmail.com"
    assert logged_in_user_id(ctx) is not None


def test_links_to_verified_local_account_keeping_role_and_password(ctx):
    uid = add_local_user(ctx, role="seller", password="passwordlokal1")
    finish(ctx)
    with ctx.app.app_context():
        u = User.query.one()
        assert u.id == uid and u.role == "seller"  # role tidak diubah, tidak ada akun ganda
        assert u.phone == "081200000001" and u.check_password("passwordlokal1")
        assert OAuthIdentity.query.one().user_id == uid
    assert logged_in_user_id(ctx) == uid


def test_unverified_local_account_is_taken_over_not_trusted(ctx):
    """Pra-pembajakan: penyerang mendaftar dgn email korban (belum verifikasi)."""
    uid = add_local_user(ctx, is_verified=False, password="passwordpenyerang")
    finish(ctx)
    with ctx.app.app_context():
        u = db.session.get(User, uid)
        assert u.is_verified
        assert not u.check_password("passwordpenyerang")  # password lama hangus
    assert logged_in_user_id(ctx) == uid


def test_inactive_account_is_rejected(ctx):
    uid = add_local_user(
        ctx,
        is_active=False,
        is_verified=False,
        password="passwordlokal1",
    )

    # Simpan kondisi akun sebelum callback Google.
    with ctx.app.app_context():
        user = db.session.get(User, uid)
        original_password_hash = user.password_hash
        original_verified = user.is_verified
        original_email = user.email
        original_role = user.role

    r = finish(ctx)

    assert r.status_code == 302
    assert r.location.endswith("/login")
    assert logged_in_user_id(ctx) is None
    assert any('reason="inactive"' in line for line in ctx.logs)

    # Pastikan akun tidak berubah dan identitas Google tidak ditambahkan.
    with ctx.app.app_context():
        user = db.session.get(User, uid)

        assert user.is_active is False
        assert user.is_verified == original_verified
        assert user.password_hash == original_password_hash
        assert user.email == original_email
        assert user.role == original_role
        assert OAuthIdentity.query.count() == 0

def test_inactive_linked_account_is_rejected(ctx):
    finish(ctx)
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    with ctx.app.app_context():
        User.query.one().is_active = False
        db.session.commit()
    finish(ctx)
    assert logged_in_user_id(ctx) is None


# ---------------------------------------------------------------- penolakan
def assert_rejected(client, response):
    assert response.status_code == 302 and response.location.endswith("/login")
    assert logged_in_user_id(client) is None
    with client.app.app_context():
        assert User.query.count() == 0 and OAuthIdentity.query.count() == 0


def test_wrong_state_is_rejected_without_calling_google(ctx):
    r = finish(ctx, state="state-palsu")
    assert_rejected(ctx, r)
    assert ctx.g["calls"] == []


def test_missing_state_is_rejected(ctx):
    r = finish(ctx, state="")
    assert_rejected(ctx, r)
    assert ctx.g["calls"] == []


def test_callback_without_starting_is_rejected(ctx):
    r = ctx.get("/auth/google/callback", query_string={"state": "x", "code": "y"})
    assert_rejected(ctx, r)
    assert ctx.g["calls"] == []


def test_state_cannot_be_replayed(ctx):
    state, nonce, _ = start(ctx)
    ctx.g["response"] = FakeResponse(200, {"id_token": make_id_token(nonce=nonce)})
    assert ctx.get("/auth/google/callback", query_string={"state": state, "code": "c"}).status_code == 302
    assert logged_in_user_id(ctx) is not None
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    n_calls = len(ctx.g["calls"])
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": "c"})
    assert r.location.endswith("/login") and logged_in_user_id(ctx) is None
    assert len(ctx.g["calls"]) == n_calls


def test_expired_flow_is_rejected(ctx):
    state, nonce, _ = start(ctx)
    with ctx.session_transaction() as s:
        s["g_started"] = int(time.time()) - g.FLOW_TTL_SECONDS - 5
    ctx.g["response"] = FakeResponse(200, {"id_token": make_id_token(nonce=nonce)})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": "c"})
    assert_rejected(ctx, r)
    assert ctx.g["calls"] == []


def test_user_cancelled_consent(ctx):
    r = finish(ctx, code=None, extra_query={"error": "access_denied"})
    assert_rejected(ctx, r)
    assert "dibatalkan" in ctx.get("/login").get_data(as_text=True)
    assert ctx.g["calls"] == []


def test_provider_error_param(ctx):
    r = finish(ctx, code=None, extra_query={"error": "server_error"})
    assert_rejected(ctx, r)


def test_missing_code(ctx):
    assert_rejected(ctx, finish(ctx, code=None))


@pytest.mark.parametrize("overrides, reason", [
    ({"nonce": "nonce-lain"}, "bad_nonce"),
    ({"nonce": None}, "bad_nonce"),
    ({"aud": "client-orang-lain"}, "bad_audience"),
    ({"aud": [CLIENT_ID, "lain"], "azp": "lain"}, "bad_audience"),
    ({"iss": "https://evil.example.com"}, "bad_issuer"),
    ({"exp": time.time() - 3600}, "expired_id_token"),
    ({"exp": None}, "missing_claim"),
    ({"iat": None}, "missing_claim"),
    ({"iss": None}, "missing_claim"),
    ({"email_verified": False}, "email_not_verified"),
    ({"email_verified": None}, "email_not_verified"),
    ({"email": None}, "missing_email"),
    ({"email": "bukan-email"}, "missing_email"),
    ({"sub": None}, "missing_claim"),
    ({"sub": ""}, "missing_sub"),
])
def test_invalid_id_token_claims_are_rejected(ctx, overrides, reason):
    r = finish(ctx, overrides)
    assert_rejected(ctx, r)
    assert any(f'reason="{reason}"' in line for line in ctx.logs), ctx.logs


def test_garbage_id_token_is_rejected(ctx):
    r = finish(ctx, token_response=FakeResponse(200, {"id_token": "bukan.jwt"}))
    assert_rejected(ctx, r)
    assert any('reason="bad_id_token"' in line for line in ctx.logs)


@pytest.mark.parametrize("response", [
    FakeResponse(400, {"error": "invalid_grant"}),
    FakeResponse(500),
    FakeResponse(200, {}),
    FakeResponse(200),  # bukan JSON
    requests.ConnectionError("down"),
    requests.Timeout("lambat"),
])
def test_token_endpoint_failures_are_handled(ctx, response):
    r = finish(ctx, token_response=response)
    assert_rejected(ctx, r)
    assert "gagal" in ctx.get("/login").get_data(as_text=True)


def test_audience_list_with_matching_azp_is_accepted(ctx):
    finish(ctx, {"aud": [CLIENT_ID, "lain"], "azp": CLIENT_ID})
    assert logged_in_user_id(ctx) is not None


# ---------------------------------------------------------------- tanda tangan ID token
def finish_with_token(client, id_token):
    state, _, _ = start(client)
    client.g["response"] = FakeResponse(200, {"id_token": id_token})
    return client.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})


def _nonce(client):
    with client.session_transaction() as s:
        return s["g_nonce"]


def _reasons(client):
    return " ".join(client.logs)


def test_token_signed_with_wrong_key_is_rejected(ctx):
    state, nonce, _ = start(ctx)
    forged = make_id_token(key=ATTACKER_KEY, nonce=nonce)  # klaim sempurna, kunci salah
    ctx.g["response"] = FakeResponse(200, {"id_token": forged})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})
    assert_rejected(ctx, r)
    assert 'reason="bad_signature"' in _reasons(ctx)


def test_tampered_payload_with_original_signature_is_rejected(ctx):
    state, nonce, _ = start(ctx)
    good = make_id_token(nonce=nonce, email="penyerang@evil.com", sub="1")
    header, _, sig = good.split(".")
    victim_payload = _b64(make_claims(nonce=nonce, email="korban@gmail.com", sub="2"))
    ctx.g["response"] = FakeResponse(200, {"id_token": f"{header}.{victim_payload}.{sig}"})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})
    assert_rejected(ctx, r)
    assert 'reason="bad_signature"' in _reasons(ctx)


@pytest.mark.parametrize("header", [{"alg": "none", "typ": "JWT"}, {"alg": "none", "kid": KID}])
def test_unsigned_alg_none_token_is_rejected(ctx, header):
    state, nonce, _ = start(ctx)
    token = f"{_b64(header)}.{_b64(make_claims(nonce=nonce))}."
    ctx.g["response"] = FakeResponse(200, {"id_token": token})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})
    assert_rejected(ctx, r)


def test_hs256_token_signed_with_public_key_is_rejected(ctx):
    """Serangan 'algorithm confusion': HMAC memakai kunci publik sebagai secret."""
    state, nonce, _ = start(ctx)
    signing_input = f"{_b64({'alg': 'HS256', 'kid': KID})}.{_b64(make_claims(nonce=nonce))}"
    mac = hmac.new(_public_pem(GOOGLE_KEY), signing_input.encode(), hashlib.sha256).digest()
    token = signing_input + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    ctx.g["response"] = FakeResponse(200, {"id_token": token})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})
    assert_rejected(ctx, r)
    assert 'reason="bad_algorithm"' in _reasons(ctx)


def test_unknown_kid_is_rejected(ctx):
    state, nonce, _ = start(ctx)
    ctx.g["response"] = FakeResponse(200, {"id_token": make_id_token(nonce=nonce, kid="kid-asing")})
    r = ctx.get("/auth/google/callback", query_string={"state": state, "code": AUTH_CODE})
    assert_rejected(ctx, r)
    assert 'reason="jwks_unavailable"' in _reasons(ctx)


def test_jwks_fetch_failure_fails_closed(ctx):
    ctx.g["jwks_error"] = True
    assert_rejected(ctx, finish(ctx))
    assert 'reason="jwks_unavailable"' in _reasons(ctx)


def test_signed_but_expired_token_is_rejected(ctx):
    assert_rejected(ctx, finish(ctx, {"exp": time.time() - 3600}))
    assert 'reason="expired_id_token"' in _reasons(ctx)


def test_real_pyjwk_client_verifies_against_published_jwks(ctx, monkeypatch):
    """Tanpa FakeJwkClient: PyJWKClient asli membaca JWKS (fetch dijaga, tanpa jaringan)."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(GOOGLE_KEY.public_key()))
    jwk.update(kid=KID, alg="RS256", use="sig")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    monkeypatch.setattr(g, "_jwk_client", None)
    monkeypatch.setattr(g, "get_jwk_client", ORIGINAL_JWK_CLIENT)
    finish(ctx)
    assert logged_in_user_id(ctx) is not None
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    assert_forged = make_id_token(key=ATTACKER_KEY, sub="777", email="x@gmail.com",
                                  nonce=_start_nonce(ctx))
    ctx.g["response"] = FakeResponse(200, {"id_token": assert_forged})
    r = ctx.get("/auth/google/callback", query_string={"state": _last_state(ctx),
                                                       "code": AUTH_CODE})
    assert r.location.endswith("/login") and logged_in_user_id(ctx) is None
    with ctx.app.app_context():
        assert User.query.count() == 1  # tidak ada akun dari token palsu


def _start_nonce(client):
    state, nonce, _ = start(client)
    client._last_state = state
    return nonce


def _last_state(client):
    return client._last_state


# ---------------------------------------------------------------- konflik identitas
def _login_as(ctx, sub, email, name="Pengguna"):
    finish(ctx, {"sub": sub, "email": email, "name": name})


def test_second_google_identity_with_same_email_is_not_merged(ctx):
    _login_as(ctx, "SUB-A", "sari@gmail.com")
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    r = finish(ctx, {"sub": "SUB-B", "email": "sari@gmail.com"})
    assert r.status_code == 302 and r.location.endswith("/login")  # bukan 500
    assert logged_in_user_id(ctx) is None
    assert 'reason="identity_conflict"' in _reasons(ctx)
    with ctx.app.app_context():
        assert User.query.count() == 1
        assert [i.subject for i in OAuthIdentity.query.all()] == ["SUB-A"]
    assert "sudah terhubung ke akun Google lain" in ctx.get("/login").get_data(as_text=True)
    # identitas aslinya tetap bisa masuk
    ctx.g["jwks_error"] = False
    finish(ctx, {"sub": "SUB-A", "email": "sari@gmail.com"})
    assert logged_in_user_id(ctx) is not None


def test_conflict_does_not_modify_existing_account(ctx):
    uid = add_local_user(ctx, is_verified=False, password="passwordlokal1")
    _login_as(ctx, "SUB-A", "sari@gmail.com")  # mengambil alih akun belum terverifikasi
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    with ctx.app.app_context():
        before = db.session.get(User, uid).password_hash
    finish(ctx, {"sub": "SUB-B", "email": "sari@gmail.com"})
    with ctx.app.app_context():
        u = db.session.get(User, uid)
        assert u.password_hash == before and OAuthIdentity.query.count() == 1


def test_race_same_identity_created_by_other_request_logs_in(ctx, monkeypatch):
    """Dua callback bersamaan: pencarian awal 'basi' (kosong), insert kena IntegrityError,
    tetapi identitas sudah dibuat permintaan lain -> masuk ke akun itu, bukan error."""
    _login_as(ctx, "SUB-A", "sari@gmail.com")
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    _stale_lookups(monkeypatch)
    r = finish(ctx, {"sub": "SUB-A", "email": "sari@gmail.com"})
    assert r.status_code == 302 and urlparse(r.location).path == "/"
    assert logged_in_user_id(ctx) is not None
    with ctx.app.app_context():
        assert User.query.count() == 1 and OAuthIdentity.query.count() == 1


def test_unresolvable_integrity_error_is_a_controlled_failure(ctx, monkeypatch):
    """IntegrityError dan identitas BUKAN milik sub ini -> gagal terkendali, bukan 500."""
    _login_as(ctx, "SUB-A", "sari@gmail.com")
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    _stale_lookups(monkeypatch)
    r = finish(ctx, {"sub": "SUB-B", "email": "sari@gmail.com"})
    assert r.status_code == 302 and r.location.endswith("/login")
    assert logged_in_user_id(ctx) is None
    assert 'reason="identity_conflict"' in _reasons(ctx)
    with ctx.app.app_context():
        assert User.query.count() == 1
        assert [i.subject for i in OAuthIdentity.query.all()] == ["SUB-A"]


def test_session_is_usable_after_a_conflict(ctx):
    """Setelah rollback, request berikutnya (DB) tetap normal."""
    _login_as(ctx, "SUB-A", "sari@gmail.com")
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    finish(ctx, {"sub": "SUB-B", "email": "sari@gmail.com"})
    finish(ctx, {"sub": "SUB-C", "email": "baru@gmail.com", "name": "Baru"})
    with ctx.app.app_context():
        assert User.query.count() == 2


def _stale_lookups(monkeypatch):
    """Panggilan PERTAMA tiap pencarian mengembalikan None (data basi); sesudahnya normal."""
    for name in ("_find_link", "_find_user_by_email"):
        real, calls = getattr(g, name), []

        def stale(arg, real=real, calls=calls):
            calls.append(arg)
            return None if len(calls) == 1 else real(arg)

        monkeypatch.setattr(g, name, stale)


# ---------------------------------------------------------------- PKCE & kebocoran
def test_token_request_uses_pkce_verifier_matching_challenge(ctx):
    _, _, q = start(ctx)
    with ctx.session_transaction() as s:
        verifier = s["g_verifier"]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == expected
    finish(ctx)
    call = ctx.g["calls"][-1]
    assert call["url"] == g.TOKEN_URL and call["timeout"]
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code"] == AUTH_CODE
    assert call["data"]["client_secret"] == CLIENT_SECRET
    assert call["data"]["redirect_uri"] == "http://localhost/auth/google/callback"
    assert len(call["data"]["code_verifier"]) >= 43


def test_secrets_and_tokens_never_reach_logs(ctx):
    finish(ctx)  # sukses
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    finish(ctx, {"aud": "salah"})  # gagal
    finish(ctx, token_response=FakeResponse(400, {"error": "invalid_grant"}))
    blob = "\n".join(ctx.logs)
    assert "google_register" in blob and "google_failed" in blob
    for secret in (CLIENT_SECRET, AUTH_CODE, FAKE_ID_TOKEN_MARK, "eyJ"):
        assert secret not in blob


def test_flow_state_is_cleared_after_callback(ctx):
    finish(ctx)
    with ctx.session_transaction() as s:
        assert not any(k in s for k in ("g_state", "g_nonce", "g_verifier", "g_started"))


# ---------------------------------------------------------------- regresi login lama
def test_logout_after_google_login_clears_session(ctx):
    finish(ctx)
    r = ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    assert r.status_code == 302
    assert logged_in_user_id(ctx) is None


def test_google_user_can_set_password_via_forgot_password(ctx, monkeypatch):
    sent = {}
    monkeypatch.setattr("app.auth.routes.send_otp_email",
                        lambda email, code, purpose: sent.update(code=code))
    finish(ctx)
    ctx.post("/logout", data={"_csrf": _csrf(ctx, "/")})
    ctx.post("/forgot-password", data={"email": "sari@gmail.com",
                                       "_csrf": _csrf(ctx, "/forgot-password")})
    ctx.post("/verify", data={"code": sent["code"], "_csrf": _csrf(ctx, "/verify")})
    r = ctx.post("/reset-password", data={"password": "passwordbaru1", "password2": "passwordbaru1",
                                          "_csrf": _csrf(ctx, "/reset-password")})
    assert r.status_code == 302
    assert _post_login(ctx, "sari@gmail.com", "passwordbaru1").status_code == 302


def test_local_login_still_works_when_google_enabled(ctx):
    add_local_user(ctx, password="passwordlokal1")
    r = _post_login(ctx, "sari@gmail.com", "passwordlokal1")
    assert r.status_code == 302 and logged_in_user_id(ctx) is not None


# ---------------------------------------------------------------- helper
def _csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    if m:
        return m.group(1)
    # halaman tanpa form (mis. "/" saat login): ambil dari sesi / halaman login
    with client.session_transaction() as s:
        if "_csrf" in s:
            return s["_csrf"]
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get("/register").get_data(as_text=True)).group(1)


def _post_login(client, ident, password):
    return client.post("/login", data={"identifier": ident, "password": password,
                                       "_csrf": _csrf(client, "/login")})

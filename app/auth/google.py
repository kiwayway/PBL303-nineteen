"""Daftar / masuk dengan Google (OpenID Connect, Authorization Code + PKCE).

Alur:
  1. GET /auth/google            -> buat state, nonce, PKCE verifier (disimpan di sesi),
                                    arahkan ke Google.
  2. GET /auth/google/callback   -> cek state, tukar `code` ke ID token langsung ke
                                    endpoint token Google (HTTPS + client secret),
                                    validasi klaim, lalu petakan ke akun Nineteen.

Catatan keamanan:
- Tanda tangan ID token (RS256) SELALU diverifikasi dengan kunci publik Google
  (JWKS) memakai PyJWT. Algoritma dikunci ke RS256 (menolak `none`/HS256). Klaim
  `iss`, `aud`, `exp`, `iat`, `nonce`, dan `email_verified` divalidasi sesudahnya.
- Konflik identitas ditangani eksplisit; akun TIDAK digabung hanya karena emailnya sama.
- Identitas dikunci ke (provider, `sub`), bukan ke email.
- Token, code, dan client secret TIDAK PERNAH dicatat ke log.
"""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import jwt
import requests
from flask import current_app, flash, redirect, request, session, url_for
from sqlalchemy.exc import IntegrityError

from ..auditlog import audit
from ..extensions import db
from ..models import OAuthIdentity, User
from .routes import bp, client_ip, current_user

PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ALLOWED_ALGORITHMS = ["RS256"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]
VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
FLOW_TTL_SECONDS = 600
HTTP_TIMEOUT = 10
CLOCK_SKEW = 60

GENERIC_ERROR = "Masuk dengan Google gagal. Silakan coba lagi."
CONFLICT_ERROR = (
    "Email ini sudah terhubung ke akun Google lain. Masuk dengan akun Google yang "
    "sebelumnya dipakai, atau gunakan email dan password."
)
_SESSION_KEYS = ("g_state", "g_nonce", "g_verifier", "g_started")


class GoogleAuthError(Exception):
    """Kegagalan alur Google. `reason` untuk audit log, `message` untuk pengguna."""

    def __init__(self, reason, message=GENERIC_ERROR):
        super().__init__(reason)
        self.reason = reason
        self.message = message


def google_enabled():
    cfg = current_app.config
    return bool(cfg.get("GOOGLE_CLIENT_ID") and cfg.get("GOOGLE_CLIENT_SECRET"))


def _redirect_uri():
    return current_app.config.get("GOOGLE_REDIRECT_URI") or url_for(
        "auth.google_callback", _external=True
    )


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _clear_flow():
    for key in _SESSION_KEYS:
        session.pop(key, None)


_jwk_client = None


def get_jwk_client():
    """Klien JWKS Google (cache kunci di memori; PyJWT mengambil ulang saat kedaluwarsa)."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(
            JWKS_URL, cache_jwk_set=True, lifespan=3600, timeout=HTTP_TIMEOUT
        )
    return _jwk_client


def verify_id_token(token, client_id):
    """Verifikasi tanda tangan RS256 + exp/aud/iat/iss wajib ada; kembalikan klaim."""
    try:
        signing_key = get_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=client_id,
            leeway=CLOCK_SKEW,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWKClientError:
        raise GoogleAuthError("jwks_unavailable")
    except jwt.InvalidSignatureError:
        raise GoogleAuthError("bad_signature")
    except jwt.ExpiredSignatureError:
        raise GoogleAuthError("expired_id_token")
    except jwt.InvalidAudienceError:
        raise GoogleAuthError("bad_audience")
    except jwt.MissingRequiredClaimError:
        raise GoogleAuthError("missing_claim")
    except jwt.InvalidAlgorithmError:
        raise GoogleAuthError("bad_algorithm")
    except jwt.PyJWTError:  # DecodeError, iat/nbf tidak valid, dst.
        raise GoogleAuthError("bad_id_token")
    if not isinstance(claims, dict):
        raise GoogleAuthError("bad_id_token")
    return claims


def validate_claims(claims, client_id, nonce, now=None):
    """Validasi klaim ID token; kembalikan dict identitas bersih."""
    now = time.time() if now is None else now
    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleAuthError("bad_issuer")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise GoogleAuthError("bad_audience")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise GoogleAuthError("bad_audience")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + CLOCK_SKEW < now:
        raise GoogleAuthError("expired_id_token")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
        raise GoogleAuthError("bad_nonce")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise GoogleAuthError("missing_sub")
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise GoogleAuthError("missing_email")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError(
            "email_not_verified", "Email Google kamu belum terverifikasi."
        )
    name = claims.get("name")
    name = name.strip() if isinstance(name, str) else ""
    email = email.strip().lower()
    return {"sub": sub, "email": email, "name": (name or email.split("@")[0])[:120]}


def exchange_code(code, verifier):
    """Tukar authorization code -> ID token (string). Satu-satunya panggilan jaringan."""
    cfg = current_app.config
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": cfg["GOOGLE_CLIENT_ID"],
                "client_secret": cfg["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException:
        raise GoogleAuthError("token_network_error")
    if resp.status_code != 200:
        raise GoogleAuthError("token_http_%s" % resp.status_code)
    try:
        id_token = resp.json().get("id_token")
    except ValueError:
        raise GoogleAuthError("token_bad_json")
    if not isinstance(id_token, str) or not id_token:
        raise GoogleAuthError("token_missing_id_token")
    return id_token


def _unusable_password_hash():
    """Hash dari string acak yang tidak disimpan: akun Google tidak bisa login
    dengan password sampai pemilik memakai 'Lupa password' (butuh akses email)."""
    from werkzeug.security import generate_password_hash

    return generate_password_hash(secrets.token_urlsafe(48))


def _find_link(subject):
    return OAuthIdentity.query.filter_by(provider=PROVIDER, subject=subject).first()


def _find_user_by_email(email):
    return User.query.filter_by(email=email).first()


def resolve_user(ident):
    """Petakan identitas Google ke User. Return (user, event).

    Raise GoogleAuthError bila identitas tidak bisa dipetakan dengan aman.
    Akun tidak pernah digabung hanya karena email sama: hanya dihubungkan bila
    akun itu belum punya identitas Google, dan Google menjamin email terverifikasi.
    """
    link = _find_link(ident["sub"])
    if link:
        return link.user, "google_login"

    try:
        user = _find_user_by_email(ident["email"])
        if user is None:
            user = User(
                name=ident["name"], email=ident["email"], phone=None,
                password_hash=_unusable_password_hash(), is_verified=True, role="customer",
            )
            db.session.add(user)
            event = "google_register"
        else:
            other = OAuthIdentity.query.filter_by(user_id=user.id, provider=PROVIDER).first()
            if other is not None:
                # Akun ini sudah dipegang identitas Google LAIN (sub berbeda).
                raise GoogleAuthError("identity_conflict", CONFLICT_ERROR)
            # Akun lokal belum terverifikasi: bisa saja didaftarkan pihak lain lebih dulu
            # (pra-pembajakan), jadi password lamanya dibuang.
            if not user.is_verified:
                user.password_hash = _unusable_password_hash()
                user.is_verified = True
            event = "google_link"
        db.session.flush()
        db.session.add(OAuthIdentity(
            user_id=user.id, provider=PROVIDER, subject=ident["sub"], email=ident["email"],
        ))
        db.session.commit()
        return user, event
    except GoogleAuthError:
        db.session.rollback()
        raise
    except IntegrityError:
        # Batasan unik menolak operasi (mis. dua callback bersamaan, atau identitas
        # lain sudah terhubung). Batalkan, lalu periksa keadaan sebenarnya SEKALI;
        # tidak ada percobaan ulang berantai.
        db.session.rollback()
        link = _find_link(ident["sub"])
        if link:  # identitas ini ternyata sudah dibuat permintaan lain
            return link.user, "google_login"
        audit("google_conflict", ip=client_ip(), detail="integrity_error")
        raise GoogleAuthError("identity_conflict", CONFLICT_ERROR)


# ------------------------------------------------------------------ routes
@bp.get("/auth/google")
def google_start():
    if current_user():
        return redirect(url_for("main.index"))
    if not google_enabled():
        flash("Masuk dengan Google belum tersedia.", "error")
        return redirect(url_for("auth.login"))

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    session["g_state"], session["g_nonce"] = state, nonce
    session["g_verifier"], session["g_started"] = verifier, int(time.time())

    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    audit("google_start", ip=client_ip())
    return redirect(AUTH_URL + "?" + urlencode(params))


@bp.get("/auth/google/callback")
def google_callback():
    state = session.get("g_state")
    nonce = session.get("g_nonce")
    verifier = session.get("g_verifier")
    started = session.get("g_started", 0)
    _clear_flow()  # sekali pakai, apa pun hasilnya

    def fail(err):
        audit("google_failed", ip=client_ip(), reason=err.reason)
        flash(err.message, "error")
        return redirect(url_for("auth.login"))

    if not google_enabled():
        return fail(GoogleAuthError("not_configured", "Masuk dengan Google belum tersedia."))
    if not (state and nonce and verifier) or time.time() - started > FLOW_TTL_SECONDS:
        return fail(GoogleAuthError("no_flow", "Sesi masuk kedaluwarsa. Silakan coba lagi."))
    sent_state = request.args.get("state", "")
    if not sent_state or not secrets.compare_digest(sent_state, state):
        return fail(GoogleAuthError("bad_state"))
    if request.args.get("error"):
        reason = "access_denied" if request.args["error"] == "access_denied" else "provider_error"
        msg = "Masuk dengan Google dibatalkan." if reason == "access_denied" else GENERIC_ERROR
        return fail(GoogleAuthError(reason, msg))
    code = request.args.get("code", "")
    if not code:
        return fail(GoogleAuthError("missing_code"))

    try:
        client_id = current_app.config["GOOGLE_CLIENT_ID"]
        id_token = exchange_code(code, verifier)
        claims = verify_id_token(id_token, client_id)
        ident = validate_claims(claims, client_id, nonce)

        # Periksa akun yang sudah terhubung terlebih dahulu.
        # Jangan memanggil resolve_user() jika akun tersebut nonaktif.
        linked_identity = _find_link(ident["sub"])

        if linked_identity is not None:
            existing_user = linked_identity.user
        else:
            # Jika belum terhubung, periksa akun lokal berdasarkan email.
            existing_user = _find_user_by_email(ident["email"])

        if existing_user is not None and not existing_user.is_active:
            raise GoogleAuthError(
                "inactive",
                "Akun dinonaktifkan. Hubungi admin.",
            )

        # Baru petakan atau buat akun setelah pemeriksaan status.
        user, event = resolve_user(ident)

    except GoogleAuthError as err:
        return fail(err)
    
    if not user.is_active:
        audit("google_failed", ip=client_ip(), reason="inactive", user_id=user.id)
        flash("Akun dinonaktifkan. Hubungi admin.", "error")
        return redirect(url_for("auth.login"))

    audit(event, ip=client_ip(), user_id=user.id)
    session.clear()
    session["user_id"] = user.id
    if event == "google_register":
        flash("Akun berhasil dibuat. Selamat datang!", "success")
    return redirect(url_for("main.index"))

from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class OtpCode(db.Model):
    """Kode 6 digit sekali pakai. Yang disimpan hanya hash-nya."""

    __tablename__ = "otp_codes"

    PURPOSE_VERIFY = "verify"
    PURPOSE_RESET = "reset"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose = db.Column(db.String(10), nullable=False)
    code_hash = db.Column(db.String(256), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    used = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", backref=db.backref("otp_codes", cascade="all, delete-orphan"))

    @staticmethod
    def new_expiry(minutes=10):
        return utcnow() + timedelta(minutes=minutes)

    def is_expired(self):
        exp = self.expires_at
        if exp.tzinfo is None:  # SQLite mengembalikan datetime tanpa zona
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < utcnow()

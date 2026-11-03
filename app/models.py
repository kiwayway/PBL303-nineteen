from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


ROLES = ("admin", "seller", "customer")
PAYMENT_STATUSES = ("pending", "waiting", "approved", "rejected")
ITEM_STATUSES = ("in_progress", "completed")

TOP_SELLER_MIN_ORDERS = 5
TOP_SELLER_MIN_RATING = 4.5


def utcnow():
    return datetime.now(timezone.utc)


def _in(column, values):
    """Teks CHECK constraint: kolom IN ('a', 'b', ...)."""
    return "%s IN (%s)" % (column, ", ".join("'%s'" % v for v in values))


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (db.CheckConstraint(_in("role", ROLES), name="ck_users_role"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    # NULL untuk akun yang dibuat lewat Google (belum mengisi nomor HP); unik bila terisi
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    role = db.Column(db.String(10), nullable=False, default="customer", server_default="customer")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    # profil seller
    avatar = db.Column(db.String(255))
    headline = db.Column(db.String(160))
    bio = db.Column(db.Text)
    focus = db.Column(db.String(255))
    location = db.Column(db.String(120))

    experiences = db.relationship("Experience", backref="seller", cascade="all, delete-orphan")
    certificates = db.relationship("Certificate", backref="seller", cascade="all, delete-orphan")
    projects = db.relationship("Project", backref="seller", cascade="all, delete-orphan")
    services = db.relationship("Service", backref="seller", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # --- statistik seller: dihitung dari data, tidak disimpan ---
    @property
    def completed_orders(self):
        return (
            db.session.query(func.count(OrderItem.id))
            .filter(OrderItem.seller_id == self.id, OrderItem.status == "completed")
            .scalar()
        )

    @property
    def avg_rating(self):
        v = (
            db.session.query(func.avg(Review.rating))
            .join(OrderItem, Review.order_item_id == OrderItem.id)
            .filter(OrderItem.seller_id == self.id)
            .scalar()
        )
        return round(float(v), 1) if v else None

    @property
    def is_top_seller(self):
        r = self.avg_rating
        return (
            self.role == "seller"
            and self.completed_orders >= TOP_SELLER_MIN_ORDERS
            and r is not None
            and r >= TOP_SELLER_MIN_RATING
        )


class OAuthIdentity(db.Model):
    """Identitas penyedia login eksternal (Google). Dikunci ke (provider, subject)
    -- `subject` adalah `sub` yang stabil, bukan email yang bisa berubah."""

    __tablename__ = "oauth_identities"
    __table_args__ = (
        db.UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),
        db.UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(254), nullable=False)  # email saat terhubung (informasi saja)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship(
        "User", backref=db.backref("oauth_identities", cascade="all, delete-orphan")
    )


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


# ---------------------------------------------------------------- marketplace

class Experience(db.Model):
    __tablename__ = "experiences"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    position = db.Column(db.String(120), nullable=False)
    institution = db.Column(db.String(120), nullable=False)
    period = db.Column(db.String(60))
    description = db.Column(db.Text)


class Certificate(db.Model):
    __tablename__ = "certificates"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    issuer = db.Column(db.String(120))
    year = db.Column(db.Integer)
    image = db.Column(db.String(255))
    credential_url = db.Column(db.String(255))


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    image = db.Column(db.String(255))
    link = db.Column(db.String(255))
    tech_stack = db.Column(db.String(255))


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    services = db.relationship("Service", backref="category")


class Service(db.Model):
    """Jasa yang dijual seller. Harga dan durasi ada di ServicePackage."""

    __tablename__ = "services"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    category_id = db.Column(db.ForeignKey("categories.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    packages = db.relationship(
        "ServicePackage", backref="service", cascade="all, delete-orphan",
        order_by="ServicePackage.price",
    )

    @property
    def min_price(self):
        """Untuk katalog: 'mulai dari'."""
        return (
            db.session.query(func.min(ServicePackage.price))
            .filter_by(service_id=self.id, is_active=True)
            .scalar()
        )

    @property
    def avg_rating(self):
        v = (
            db.session.query(func.avg(Review.rating))
            .join(OrderItem, Review.order_item_id == OrderItem.id)
            .join(ServicePackage, OrderItem.package_id == ServicePackage.id)
            .filter(ServicePackage.service_id == self.id)
            .scalar()
        )
        return round(float(v), 1) if v else None

    @property
    def sold_count(self):
        return (
            db.session.query(func.count(OrderItem.id))
            .join(ServicePackage, OrderItem.package_id == ServicePackage.id)
            .join(Order, OrderItem.order_id == Order.id)
            .filter(ServicePackage.service_id == self.id, Order.status == "approved")
            .scalar()
        )


class ServicePackage(db.Model):
    __tablename__ = "service_packages"
    __table_args__ = (
        db.CheckConstraint("price > 0", name="ck_package_price_positive"),
        db.CheckConstraint("duration_days > 0", name="ck_package_duration_positive"),
    )
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.ForeignKey("services.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)  # mis. Basic / Standard / Premium
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)  # rupiah
    duration_days = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())


class Cart(db.Model):
    __tablename__ = "carts"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.ForeignKey("users.id"), unique=True, nullable=False)
    items = db.relationship("CartItem", backref="cart", cascade="all, delete-orphan")


class CartItem(db.Model):
    __tablename__ = "cart_items"
    __table_args__ = (db.UniqueConstraint("cart_id", "package_id"),)
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.ForeignKey("carts.id"), nullable=False)
    package_id = db.Column(db.ForeignKey("service_packages.id"), nullable=False)
    package = db.relationship("ServicePackage")


class Order(db.Model):
    """Satu kali checkout. Status di sini = status PEMBAYARAN."""

    __tablename__ = "orders"
    __table_args__ = (
        db.CheckConstraint(_in("status", PAYMENT_STATUSES), name="ck_orders_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default="pending", server_default="pending")
    total = db.Column(db.Integer, nullable=False, default=0)
    proof_path = db.Column(db.String(255))
    note = db.Column(db.String(255))  # alasan ditolak
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    customer = db.relationship("User")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    """Satu paket jasa yang dibeli. Status di sini = status PENGERJAAN."""

    __tablename__ = "order_items"
    __table_args__ = (
        db.CheckConstraint(_in("status", ITEM_STATUSES), name="ck_order_items_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.ForeignKey("orders.id"), nullable=False, index=True)
    package_id = db.Column(db.ForeignKey("service_packages.id"), nullable=False, index=True)
    seller_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)  # snapshot
    price = db.Column(db.Integer, nullable=False)  # snapshot harga saat checkout
    status = db.Column(db.String(11), nullable=False, default="in_progress", server_default="in_progress")
    completed_at = db.Column(db.DateTime(timezone=True))

    package = db.relationship("ServicePackage")
    seller = db.relationship("User", foreign_keys=[seller_id])
    messages = db.relationship(
        "Message", backref="order_item", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    review = db.relationship(
        "Review", backref="order_item", uselist=False, cascade="all, delete-orphan"
    )


class Message(db.Model):
    """Chat customer-seller, satu percakapan per OrderItem.
    Hanya boleh dibuka jika Order sudah 'approved' (dicek di route)."""

    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.ForeignKey("order_items.id"), nullable=False, index=True)
    sender_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sender = db.relationship("User")


class Review(db.Model):
    """Satu review per OrderItem; hanya jika OrderItem 'completed' (dicek di route)."""

    __tablename__ = "reviews"
    __table_args__ = (db.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"),)
    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.ForeignKey("order_items.id"), unique=True, nullable=False)
    customer_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    customer = db.relationship("User")


class ContactInquiry(db.Model):
    """Pesan yang dikirim pengunjung melalui halaman Contact Us."""

    __tablename__ = "contact_inquiries"
    __table_args__ = (
        db.CheckConstraint("length(name) BETWEEN 2 AND 120", name="ck_contact_name_length"),
        db.CheckConstraint("length(email) BETWEEN 5 AND 254", name="ck_contact_email_length"),
        db.CheckConstraint("length(subject) BETWEEN 3 AND 120", name="ck_contact_subject_length"),
        db.CheckConstraint("length(message) BETWEEN 10 AND 5000", name="ck_contact_message_length"),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, index=True)
    subject = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="new", server_default="new", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

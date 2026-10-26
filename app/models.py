from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, UniqueConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

TOP_SELLER_MIN_ORDERS = 5
TOP_SELLER_MIN_RATING = 4.5

def utcnow():
    return datetime.now(timezone.utc)

class Role:
    CUSTOMER = "customer"
    SELLER = "seller"
    ADMIN = "admin"

class OrderStatus:
    PENDING = "pending"
    AWAITING = "menunggu_verifikasi"
    APPROVED = "approved"
    REJECTED = "ditolak"

class ItemStatus:
    IN_PROGRESS = "dikerjakan"
    COMPLETED = "completed"

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), unique=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255))
    google_id = db.Column(db.String(64), unique=True)
    role = db.Column(db.String(20), nullable=False, default=Role.CUSTOMER)
    active = db.Column(db.Boolean, nullable=False, default=True)
    avatar = db.Column(db.String(255))
    headline = db.Column(db.String(160))
    bio = db.Column(db.Text)
    focus = db.Column(db.String(255))
    location = db.Column(db.String(120))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    experiences = db.relationship("Experience", backref="seller", cascade="all, delete-orphan")
    certificates = db.relationship("Certificate", backref="seller", cascade="all, delete-orphan")
    projects = db.relationship("Project", backref="seller", cascade="all, delete-orphan")
    services = db.relationship("Service", backref="seller", cascade="all, delete-orphan")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)

    @property
    def avg_rating(self):
        value = (
            db.session.query(func.avg(Review.rating))
            .join(Service, Review.service_id == Service.id)
            .filter(Service.seller_id == self.id)
            .scalar()
        )
        return round(float(value), 1) if value else None

    @property
    def completed_count(self):
        return (
            db.session.query(func.count(OrderItem.id))
            .join(Order, OrderItem.order_id == Order.id)
            .join(Service, OrderItem.service_id == Service.id)
            .filter(
                Service.seller_id == self.id,
                Order.status == OrderStatus.APPROVED,
                OrderItem.status == ItemStatus.COMPLETED,
            )
            .scalar()
        )

    @property
    def is_top_seller(self):
        return (
            self.completed_count >= TOP_SELLER_MIN_ORDERS
            and (self.avg_rating or 0) >= TOP_SELLER_MIN_RATING
        )

class Experience(db.Model):
    __tablename__ = "experiences"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    position = db.Column(db.String(120), nullable=False)
    institution = db.Column(db.String(120), nullable=False)
    period = db.Column(db.String(60))
    description = db.Column(db.Text)

class Certificate(db.Model):
    __tablename__ = "certificates"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    issuer = db.Column(db.String(120))
    year = db.Column(db.Integer)
    image = db.Column(db.String(255))
    credential_url = db.Column(db.String(255))

class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
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
    __tablename__ = "services"
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    packages = db.relationship(
        "ServicePackage", backref="service", cascade="all, delete-orphan",
        order_by="ServicePackage.price",
    )
    reviews = db.relationship("Review", backref="service", cascade="all, delete-orphan")

    @property
    def min_price(self):
        return db.session.query(func.min(ServicePackage.price)).filter(
            ServicePackage.service_id == self.id
        ).scalar()

    @property
    def avg_rating(self):
        value = db.session.query(func.avg(Review.rating)).filter(Review.service_id == self.id).scalar()
        return round(float(value), 1) if value else None

    @property
    def completed_count(self):
        return (
            db.session.query(func.count(OrderItem.id))
            .join(Order, OrderItem.order_id == Order.id)
            .filter(
                OrderItem.service_id == self.id,
                Order.status == OrderStatus.APPROVED,
                OrderItem.status == ItemStatus.COMPLETED,
            )
            .scalar()
        )

class ServicePackage(db.Model):
    __tablename__ = "service_packages"
    __table_args__ = (
        UniqueConstraint("service_id", "name"),
        CheckConstraint("price > 0", name="ck_package_price"),
        CheckConstraint("duration_days > 0", name="ck_package_duration"),
    )
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False, default=1)

class Cart(db.Model):
    __tablename__ = "carts"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    items = db.relationship("CartItem", backref="cart", cascade="all, delete-orphan")

class CartItem(db.Model):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "package_id"),)
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey("carts.id"), nullable=False)
    package_id = db.Column(db.Integer, db.ForeignKey("service_packages.id"), nullable=False)
    package = db.relationship("ServicePackage")

class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default=OrderStatus.PENDING, index=True)
    total = db.Column(db.Integer, nullable=False, default=0)
    proof_path = db.Column(db.String(255))
    reject_reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    reviewed_at = db.Column(db.DateTime(timezone=True))

    customer = db.relationship("User")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")

class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("service_packages.id"), nullable=False)
    package_name = db.Column(db.String(60), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=ItemStatus.IN_PROGRESS)
    completed_at = db.Column(db.DateTime(timezone=True))

    service = db.relationship("Service")
    package = db.relationship("ServicePackage")
    messages = db.relationship("Message", backref="order_item", cascade="all, delete-orphan",
                               order_by="Message.created_at")

class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    sender = db.relationship("User")

class Review(db.Model):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("order_item_id"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    customer = db.relationship("User")
    order_item = db.relationship("OrderItem")
import re
import secrets

import click
from sqlalchemy import or_

from .auth.routes import EMAIL_RE, normalize_phone
from .extensions import db
from .models import (
    Category, Certificate, Experience, Order, OrderItem, Project, Review, Service,
    ServicePackage, User,
)

DEFAULT_CATEGORIES = [
    "Web Development",
    "Desain Grafis",
    "Video & Animasi",
    "Penulisan & Penerjemahan",
    "Digital Marketing",
    "Cybersecurity",
]


def _create_user(role, name, email, phone, password):
    name = name.strip()
    email = email.strip().lower()
    phone = normalize_phone(phone)

    if not name:
        raise click.ClickException("Nama wajib diisi.")
    if not EMAIL_RE.match(email):
        raise click.ClickException("Format email tidak valid.")
    if not re.fullmatch(r"0\d{8,13}", phone):
        raise click.ClickException("Nomor HP tidak valid.")
    if len(password) < 8:
        raise click.ClickException("Password minimal 8 karakter.")
    if User.query.filter(or_(User.email == email, User.phone == phone)).first():
        raise click.ClickException("Email atau nomor HP sudah terdaftar.")

    # dibuat lewat CLI oleh orang yang berwenang, jadi langsung terverifikasi
    user = User(name=name, email=email, phone=phone, role=role, is_verified=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"{role} dibuat: {user.email} ({user.phone})")


def _user_options(f):
    f = click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)(f)
    f = click.option("--phone", prompt=True)(f)
    f = click.option("--email", prompt=True)(f)
    f = click.option("--name", prompt=True)(f)
    return f


def register_cli(app):
    @app.cli.command("create-admin")
    @_user_options
    def create_admin(name, email, phone, password):
        """Buat akun admin (langsung terverifikasi)."""
        _create_user("admin", name, email, phone, password)

    @app.cli.command("create-seller")
    @_user_options
    def create_seller(name, email, phone, password):
        """Buat akun seller (langsung terverifikasi)."""
        _create_user("seller", name, email, phone, password)

    @app.cli.command("seed-categories")
    def seed_categories():
        """Isi kategori jasa awal (aman dijalankan berulang)."""
        added = 0
        for name in DEFAULT_CATEGORIES:
            if not Category.query.filter_by(name=name).first():
                db.session.add(Category(name=name))
                added += 1
        db.session.commit()
        click.echo(f"{added} kategori ditambahkan, {len(DEFAULT_CATEGORIES) - added} sudah ada.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """DATA CONTOH untuk dev lokal: 1 seller lengkap + 3 customer + review.

        Akun demo diberi password acak yang tidak diketahui siapa pun, jadi tidak
        bisa dipakai login; halaman profil seller tetap bisa dilihat publik.
        """
        if User.query.filter_by(email="demo.seller@nineteen.local").first():
            click.echo("Data demo sudah ada.")
            return

        def make_user(name, email, phone, role, **extra):
            u = User(name=name, email=email, phone=phone, role=role, is_verified=True, **extra)
            u.set_password(secrets.token_urlsafe(24))
            db.session.add(u)
            return u

        seller = make_user(
            "Dimas Pratama", "demo.seller@nineteen.local", "081200000001", "seller",
            headline="Full-stack Developer & Cybersecurity Enthusiast",
            bio=("Mahasiswa Cybersecurity Engineering yang suka membangun web aman.\n"
                 "Sudah mengerjakan website UMKM, sistem booking, dan dashboard internal."),
            focus="Web Development, Flask, Laravel, Keamanan Aplikasi",
            location="Batam, Kepulauan Riau",
        )
        customers = [
            make_user(n, f"demo.cust{i}@nineteen.local", f"08120000010{i}", "customer")
            for i, n in enumerate(["Sari Wulandari", "Budi Santoso", "Maya"], start=1)
        ]
        cat = Category.query.filter_by(name="Web Development").first()
        if cat is None:
            cat = Category(name="Web Development")
            db.session.add(cat)
        db.session.flush()

        db.session.add_all([
            Experience(seller_id=seller.id, position="IT Support", institution="PT Contoh Batam",
                       period="2022 - 2023", description="Menangani jaringan kantor dan helpdesk."),
            Experience(seller_id=seller.id, position="Freelance Web Developer",
                       institution="Mandiri", period="2023 - sekarang",
                       description="Membuat website dan sistem informasi untuk klien lokal."),
            Certificate(seller_id=seller.id, name="Dasar Keamanan Siber", issuer="Polibatam", year=2025),
            Project(seller_id=seller.id, title="Sistem Booking Tiket Ferry",
                    description="Aplikasi pemesanan tiket dengan persetujuan admin.",
                    tech_stack="Flask, PostgreSQL, Docker"),
            Project(seller_id=seller.id, title="Toko Online UMKM",
                    description="E-commerce sederhana dengan pembayaran manual.",
                    tech_stack="Laravel, MySQL"),
        ])

        packages = []
        for title, prices in [("Website Company Profile", (350000, 750000)),
                              ("Audit Keamanan Website", (500000, 1200000))]:
            svc = Service(seller_id=seller.id, category_id=cat.id, title=title,
                          description=f"{title} oleh {seller.name}.")
            db.session.add(svc)
            db.session.flush()
            for name, price in zip(("Basic", "Premium"), prices):
                pkg = ServicePackage(service_id=svc.id, name=name, price=price, duration_days=5)
                db.session.add(pkg)
                packages.append(pkg)
        db.session.flush()

        reviews = [
            (5, "Hasil rapi dan selesai tepat waktu. Komunikasinya enak."),
            (5, "Mantap, website langsung jadi dan mudah dipakai."),
            (4, "Bagus, ada sedikit revisi tapi cepat ditangani."),
        ]
        for cust, pkg, (rating, comment) in zip(customers, packages, reviews):
            order = Order(customer_id=cust.id, status="approved", total=pkg.price)
            db.session.add(order)
            db.session.flush()
            item = OrderItem(order_id=order.id, package_id=pkg.id, seller_id=seller.id,
                             price=pkg.price, status="completed")
            db.session.add(item)
            db.session.flush()
            db.session.add(Review(order_item_id=item.id, customer_id=cust.id,
                                  rating=rating, comment=comment))
        db.session.commit()
        click.echo(f"Data demo dibuat. Buka /seller/{seller.id}")

    @app.cli.command("auto-complete")
    def auto_complete():
        """Selesaikan otomatis pesanan yang hasilnya sudah dikirim dan melewati batas waktu.

        Jalankan berkala (mis. tiap 15 menit lewat cron / service worker di docker-compose).
        """
        from .finance.service import auto_complete_due
        click.echo(f"{auto_complete_due()} pesanan diselesaikan otomatis.")

import re

import click
from sqlalchemy import or_

from .auth.routes import EMAIL_RE, normalize_phone
from .extensions import db
from .models import Category, User

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

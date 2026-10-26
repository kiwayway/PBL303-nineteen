import os
from flask import Flask, render_template, request, redirect, url_for, flash
from app.extensions import db, migrate

def create_app(test_config=None):
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///:memory:"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    
    if test_config:
        app.config.update(test_config)  

    db.init_app(app)
    migrate.init_app(app, db)

    with app.app_context():
        from app import models 
        db.create_all()

    @app.route('/')
    @app.route('/login')
    def login_page():
        return render_template('login.html')

    # Rute Register dengan Logika POST
    @app.route('/register', methods=['GET', 'POST'])
    def register_page():
        from app.models import User # Import model User
        
        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            phone = request.form.get('phone')
            password = request.form.get('password')

            # Validasi jika email atau nomor HP sudah ada
            existing_user = User.query.filter((User.email == email) | (User.phone == phone)).first()
            if existing_user:
                flash('Pendaftaran gagal: Email atau Nomor HP sudah terdaftar.', 'error')
                return redirect(url_for('register_page'))

            # Buat User baru (sesuai field di models.py)
            new_user = User(
                name=name,
                email=email,
                phone=phone
            )
            # Hash password menggunakan fungsi dari model
            new_user.set_password(password)

            # Simpan ke Database
            db.session.add(new_user)
            db.session.commit()

            flash('Akun berhasil dibuat! Silakan login.', 'success')
            return redirect(url_for('login_page'))

        return render_template('register.html')

    @app.route('/forgot-password')
    def forgot_password_page():
        return render_template('forgot-password.html')

    @app.route('/verify')
    def verify_page():
        return render_template('verify.html')

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app
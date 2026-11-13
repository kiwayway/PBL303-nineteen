import re

import pytest

from app import create_app
from app.extensions import db
from app.models import Category, Order, Service, ServicePackage, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(role="admin", email=None):
    n = User.query.count() + 1
    user = User(
        name=f"User {n}", email=email or f"user{n}@example.com",
        phone=f"0812345678{n:02d}", role=role, is_verified=True,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def logged_client(app, user):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user.id
    return client


def csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def test_admin_dashboard_requires_login_and_admin_role(app):
    client = app.test_client()
    assert client.get("/admin/").status_code == 302

    customer = make_user("customer")
    assert logged_client(app, customer).get("/admin/").status_code == 403


def test_admin_dashboard_shows_live_marketplace_stats(app):
    admin = make_user("admin")
    make_user("seller")
    make_user("customer")
    client = logged_client(app, admin)
    response = client.get("/admin/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Dashboard Admin" in html
    assert "Total Pengguna" in html
    assert "Total Seller" in html


def test_admin_login_redirects_to_dashboard(app):
    admin = make_user("admin")
    client = app.test_client()
    login_html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="_csrf" value="([^"]+)"', login_html).group(1)
    response = client.post("/login", data={
        "identifier": admin.email, "password": "password123", "_csrf": token,
    })
    assert response.status_code == 302
    assert response.location.endswith("/admin/")


def test_admin_can_toggle_customer_but_not_self(app):
    admin = make_user("admin")
    customer = make_user("customer")
    client = logged_client(app, admin)
    token = csrf(client, "/admin/users")
    response = client.post(f"/admin/users/{customer.id}/toggle", data={"_csrf": token})
    assert response.status_code == 302
    assert db.session.get(User, customer.id).is_active is False

    token = csrf(client, "/admin/users")
    client.post(f"/admin/users/{admin.id}/toggle", data={"_csrf": token})
    assert db.session.get(User, admin.id).is_active is True


def test_admin_cannot_disable_last_active_admin(app):
    admin = make_user("admin")
    second_admin = make_user("admin")
    client = logged_client(app, admin)
    token = csrf(client, "/admin/users")
    client.post(f"/admin/users/{second_admin.id}/toggle", data={"_csrf": token})
    assert db.session.get(User, second_admin.id).is_active is False

    token = csrf(client, "/admin/users")
    client.post(f"/admin/users/{admin.id}/toggle", data={"_csrf": token})
    assert db.session.get(User, admin.id).is_active is True


def test_admin_can_toggle_service(app):
    admin = make_user("admin")
    seller = make_user("seller")
    category = Category(name="Desain")
    db.session.add(category)
    db.session.flush()
    service = Service(seller_id=seller.id, category_id=category.id,
                      title="Desain logo", description="Deskripsi")
    db.session.add(service)
    db.session.commit()
    client = logged_client(app, admin)
    token = csrf(client, "/admin/services")
    response = client.post(f"/admin/services/{service.id}/toggle", data={"_csrf": token})
    assert response.status_code == 302
    assert db.session.get(Service, service.id).is_active is False


def test_admin_category_management(app):
    admin = make_user("admin")
    client = logged_client(app, admin)
    token = csrf(client, "/admin/categories")
    response = client.post("/admin/categories", data={"name": "UI/UX", "_csrf": token})
    assert response.status_code == 302
    category = Category.query.filter_by(name="UI/UX").one()

    token = csrf(client, "/admin/categories")
    response = client.post(f"/admin/categories/{category.id}/delete", data={"_csrf": token})
    assert response.status_code == 302
    assert db.session.get(Category, category.id) is None


def test_non_admin_cannot_access_admin_pages(app):
    customer = make_user("customer")
    client = logged_client(app, customer)
    for path in ["/admin/", "/admin/users", "/admin/services", "/admin/orders",
                 "/admin/reviews", "/admin/categories"]:
        assert client.get(path).status_code == 403


def _png_upload(color=(30, 120, 220)):
    from io import BytesIO
    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (32, 32), color).save(stream, format="PNG")
    stream.seek(0)
    return stream

def _image_upload(image_format="PNG"):
    from io import BytesIO
    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (32, 32), (50, 120, 200)).save(
        stream, format=image_format
    )
    stream.seek(0)
    return stream

def test_admin_can_upload_category_logo_and_background(app):
    admin = make_user("admin")
    client = logged_client(app, admin)
    token = csrf(client, "/admin/categories")
    response = client.post("/admin/categories", data={
        "name": "Jasa Kesehatan",
        "_csrf": token,
        "logo_image": (_png_upload(), "logo.png", "image/png"),
        "background_image": (_png_upload((20, 160, 80)), "cover.png", "image/png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    category = Category.query.filter_by(name="Jasa Kesehatan").one()
    assert category.logo_image.startswith("uploads/categories/")
    assert category.background_image.startswith("uploads/categories/")
    assert (app.root_path + "/static/" + category.logo_image)
    assert client.get("/").status_code == 200
    assert category.logo_image in client.get("/").get_data(as_text=True)


def test_admin_rejects_non_image_even_with_image_extension(app):
    from io import BytesIO

    admin = make_user("admin")
    client = logged_client(app, admin)
    token = csrf(client, "/admin/categories")
    response = client.post("/admin/categories", data={
        "name": "Kategori Aman",
        "_csrf": token,
        "logo_image": (BytesIO(b"<script>alert(1)</script>"), "fake.png", "image/png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert Category.query.filter_by(name="Kategori Aman").first() is None
    assert "File tidak dapat dibaca sebagai gambar yang valid" in response.get_data(as_text=True)


def test_admin_can_replace_and_remove_category_images(app):
    admin = make_user("admin")
    category = Category(name="Kategori Gambar")
    db.session.add(category)
    db.session.commit()
    client = logged_client(app, admin)
    token = csrf(client, "/admin/categories")
    response = client.post(f"/admin/categories/{category.id}/images", data={
        "_csrf": token,
        "logo_image": (_png_upload(), "new.png", "image/png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    category = db.session.get(Category, category.id)
    assert category.logo_image
    token = csrf(client, "/admin/categories")
    client.post(f"/admin/categories/{category.id}/images", data={
        "_csrf": token, "remove_logo_image": "1",
    })
    assert db.session.get(Category, category.id).logo_image is None


def _valid_service_form_data(admin_client, category, seller):
    return {
        "_csrf": csrf(admin_client, "/admin/services/new"),
        "title": "Jasa desain logo profesional",
        "description": "Layanan desain logo profesional sesuai kebutuhan pelanggan.",
        "category_id": str(category.id),
        "seller_id": str(seller.id),
        "package_name": "Paket Basic",
        "price": "150000",
        "duration_days": "3",
        "is_active": "1",
    }


def test_admin_can_upload_valid_service_image(app):
    from app.models import Service

    admin = make_user("admin")
    seller = make_user("seller")
    category = Category(name="Desain")
    db.session.add(category)
    db.session.commit()

    client = logged_client(app, admin)
    data = _valid_service_form_data(client, category, seller)
    data["image"] = (_image_upload("PNG"), "logo.png", "image/png")

    response = client.post(
        "/admin/services/new",
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 302

    service = Service.query.one()
    assert service.image.startswith("uploads/services/")
    assert service.image.endswith(".png")

    from pathlib import Path
    saved = Path(app.static_folder) / service.image
    assert saved.is_file()


def test_admin_rejects_fake_service_image(app):
    from io import BytesIO
    from app.models import Service

    admin = make_user("admin")
    seller = make_user("seller")
    category = Category(name="Desain")
    db.session.add(category)
    db.session.commit()

    client = logged_client(app, admin)
    data = _valid_service_form_data(client, category, seller)
    data["image"] = (
        BytesIO(b"<script>alert('xss')</script>"),
        "fake.png",
        "image/png",
    )

    response = client.post(
        "/admin/services/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert Service.query.count() == 0
    assert "File gambar rusak atau tidak valid" in response.get_data(as_text=True)


def test_admin_rejects_svg_service_image(app):
    from io import BytesIO
    from app.models import Service

    admin = make_user("admin")
    seller = make_user("seller")
    category = Category(name="Desain")
    db.session.add(category)
    db.session.commit()

    client = logged_client(app, admin)
    data = _valid_service_form_data(client, category, seller)
    data["image"] = (
        BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        "image.svg",
        "image/svg+xml",
    )

    response = client.post(
        "/admin/services/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert Service.query.count() == 0
    html = response.get_data(as_text=True)
    assert (
        "File gambar rusak atau tidak valid" in html
        or "Gunakan gambar PNG, JPEG, atau WebP" in html
    )


def test_admin_rejects_oversized_service_image(app):
    from io import BytesIO
    from app.models import Service

    admin = make_user("admin")
    seller = make_user("seller")
    category = Category(name="Desain")
    db.session.add(category)
    db.session.commit()

    client = logged_client(app, admin)
    data = _valid_service_form_data(client, category, seller)
    data["image"] = (
        BytesIO(b"A" * (4 * 1024 * 1024 + 1)),
        "large.png",
        "image/png",
    )

    response = client.post(
        "/admin/services/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert Service.query.count() == 0
    assert "Gambar maksimal 4 MB" in response.get_data(as_text=True)

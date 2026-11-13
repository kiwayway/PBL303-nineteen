import re
from io import BytesIO

import pytest
from app import create_app
from app.extensions import db
from app.models import (
    User,
    Category,
    Service,
    ServicePackage,
    Order,
    OrderItem,
)

@pytest.fixture
def app():
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

def user(role):
    n=User.query.count()+1
    u=User(name=f"User {n}",email=f"u{n}@example.test",phone=f"08120000{n:04d}",role=role,is_verified=True)
    u.set_password("password123"); db.session.add(u); db.session.commit(); return u

def client_for(app,u):
    c=app.test_client()
    with c.session_transaction() as sess: sess["user_id"]=u.id
    return c

def token(client,path="/jasa"):
    html=client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"',html).group(1)


def fixture_service(seller, title="Desain logo profesional"):
    c = Category.query.filter_by(name="Desain").first()

    if not c:
        c = Category(name="Desain")
        db.session.add(c)
        db.session.flush()

    s = Service(
        seller_id=seller.id,
        category_id=c.id,
        title=title,
        description="Layanan desain logo sesuai brief pelanggan.",
    )

    s.packages.append(
        ServicePackage(
            name="Basic",
            price=150000,
            duration_days=3,
        )
    )

    db.session.add(s)
    db.session.commit()

    return s


def fixture_order_item(
    seller,
    customer,
    payment_status="waiting",
    service_title="Desain logo profesional",
):
    service = fixture_service(seller, service_title)
    package = service.packages[0]

    order = Order(
        customer_id=customer.id,
        status=payment_status,
        total=package.price,
    )
    db.session.add(order)
    db.session.flush()

    item = OrderItem(
        order_id=order.id,
        package_id=package.id,
        seller_id=seller.id,
        price=package.price,
        service_title=service.title,
        package_name=package.name,
        duration_days=package.duration_days,
    )

    db.session.add(item)
    db.session.commit()

    return order, item

def test_catalog_search_and_detail(app):
    seller=user("seller"); service=fixture_service(seller)
    c=app.test_client()
    assert c.get("/jasa?q=logo").status_code==200
    assert c.get(f"/jasa/{service.id}").status_code==200
    assert c.get("/jasa?q=not-found").status_code==200

def test_cart_checkout_uses_database_price_and_is_customer_owned(app):
    seller=user("seller"); service=fixture_service(seller)
    customer=user("customer"); c=client_for(app,customer)
    package=service.packages[0]
    response=c.post(f"/keranjang/tambah/{package.id}",data={"_csrf":token(c)})
    assert response.status_code==302
    response=c.post("/checkout",data={"_csrf":token(c,"/checkout"),"note":"ignored"})
    assert response.status_code==302
    order=Order.query.one()
    assert order.customer_id==customer.id and order.total==150000 and order.customer_note=="ignored"
    assert order.items[0].price==150000
    assert order.items[0].service_title=="Desain logo profesional"
    other=client_for(app,user("customer"))
    assert other.get(f"/pesanan/{order.id}/pembayaran").status_code==404

def test_payment_upload_and_admin_approval(app):
    from app.models import OrderItem
    seller=user("seller"); service=fixture_service(seller); customer=user("customer")
    order=Order(customer_id=customer.id,status="pending",total=150000)
    db.session.add(order); db.session.flush()
    db.session.add(OrderItem(order_id=order.id,package_id=service.packages[0].id,seller_id=seller.id,price=150000,service_title=service.title,package_name="Basic",duration_days=3))
    db.session.commit()
    c=client_for(app,customer); token_value=token(c,f"/pesanan/{order.id}/pembayaran")
    # PNG signature-only payload is rejected by image decoder.
    bad=c.post(f"/pesanan/{order.id}/bukti",data={"_csrf":token_value,"proof":(BytesIO(b"\x89PNG\r\n\x1a\ninvalid"),"x.png")},content_type="multipart/form-data")
    assert bad.status_code==400


def test_seller_cannot_edit_another_sellers_service(app):
    seller_a=user("seller"); service=fixture_service(seller_a)
    seller_b=user("seller"); c=client_for(app,seller_b)
    assert c.get(f"/seller/jasa/{service.id}/edit").status_code==404
    assert c.get("/seller/jasa").status_code==200

def test_non_seller_cannot_access_seller_workspace(app):
    customer=user("customer"); c=client_for(app,customer)
    assert c.get("/seller/jasa").status_code==403
    assert c.get("/seller/pesanan").status_code==403


def test_payment_proof_download_is_admin_only_and_attachment(app):
    from pathlib import Path

    seller = user("seller")
    service = fixture_service(seller)
    customer = user("customer")
    admin = user("admin")

    order = Order(
        customer_id=customer.id,
        status="waiting",
        total=150000,
        proof_path="test-proof.pdf",
    )
    db.session.add(order)
    db.session.commit()

    # Buat file bukti pembayaran sementara.
    proof_dir = Path(app.instance_path) / "private_uploads" / "payments"
    proof_dir.mkdir(parents=True, exist_ok=True)
    (proof_dir / "test-proof.pdf").write_bytes(b"%PDF-1.4 test")

    # Customer tidak boleh mengakses route khusus admin.
    customer_client = client_for(app, customer)
    response = customer_client.get(
        f"/admin/orders/{order.id}/proof"
    )
    assert response.status_code == 403

    # Admin boleh mengunduh file.
    admin_client = client_for(app, admin)
    response = admin_client.get(
        f"/admin/orders/{order.id}/proof"
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"].lower()
    assert "bukti-pembayaran.pdf" in response.headers["Content-Disposition"]
    assert response.data == b"%PDF-1.4 test"


def test_admin_can_approve_payment_once(app):
    seller = user("seller")
    customer = user("customer")
    admin = user("admin")

    order, item = fixture_order_item(seller, customer, "waiting")

    client = client_for(app, admin)
    csrf = token(client, "/admin/orders")

    response = client.post(
        f"/admin/orders/{order.id}/decision",
        data={"_csrf": csrf, "decision": "approve"},
    )

    assert response.status_code == 302

    db.session.refresh(order)
    assert order.status == "approved"

    # Keputusan kedua untuk pesanan yang sama harus ditolak.
    response = client.post(
        f"/admin/orders/{order.id}/decision",
        data={"_csrf": csrf, "decision": "approve"},
    )

    assert response.status_code == 409


def test_admin_can_reject_payment_with_valid_reason(app):
    seller = user("seller")
    customer = user("customer")
    admin = user("admin")

    order, item = fixture_order_item(seller, customer, "waiting")

    client = client_for(app, admin)
    csrf = token(client, "/admin/orders")

    # Alasan terlalu pendek tidak boleh diterima.
    response = client.post(
        f"/admin/orders/{order.id}/decision",
        data={"_csrf": csrf, "decision": "reject", "reason": "No"},
    )

    assert response.status_code == 302

    db.session.refresh(order)
    assert order.status == "waiting"

    # Alasan yang valid harus diterima.
    response = client.post(
        f"/admin/orders/{order.id}/decision",
        data={
            "_csrf": csrf,
            "decision": "reject",
            "reason": "Bukti pembayaran tidak sesuai.",
        },
    )

    assert response.status_code == 302

    db.session.refresh(order)
    assert order.status == "rejected"
    assert order.note == "Bukti pembayaran tidak sesuai."


def test_non_admin_cannot_decide_payment(app):
    seller = user("seller")
    customer = user("customer")

    order, item = fixture_order_item(seller, customer, "waiting")

    client = client_for(app, customer)
    csrf = token(client)

    response = client.post(
        f"/admin/orders/{order.id}/decision",
        data={
            "_csrf": csrf,
            "decision": "approve",
        },
    )

    assert response.status_code == 403

    db.session.refresh(order)
    assert order.status == "waiting"


def test_seller_can_complete_approved_own_order(app):
    seller = user("seller")
    customer = user("customer")

    order, item = fixture_order_item(seller, customer, "approved")

    client = client_for(app, seller)
    csrf = token(client, "/seller/pesanan")

    response = client.post(
        f"/seller/pesanan/{item.id}/selesai",
        data={"_csrf": csrf},
    )

    assert response.status_code == 302

    db.session.refresh(item)
    assert item.status == "completed"
    assert item.completed_at is not None


def test_seller_cannot_complete_unapproved_or_other_seller_order(app):
    seller_a = user("seller")
    seller_b = user("seller")
    customer = user("customer")

    # Pesanan seller A belum disetujui.
    pending_order, pending_item = fixture_order_item(
        seller_a, customer, "waiting"
    )

    client_a = client_for(app, seller_a)
    csrf_a = token(client_a, "/seller/pesanan")

    response = client_a.post(
        f"/seller/pesanan/{pending_item.id}/selesai",
        data={"_csrf": csrf_a},
    )

    assert response.status_code == 409

    db.session.refresh(pending_item)
    assert pending_item.status == "in_progress"

    # Seller B tidak boleh menyelesaikan pesanan milik seller A.
    approved_order, approved_item = fixture_order_item(
        seller_a, customer, "approved"
    )

    client_b = client_for(app, seller_b)
    csrf_b = token(client_b, "/seller/pesanan")

    response = client_b.post(
        f"/seller/pesanan/{approved_item.id}/selesai",
        data={"_csrf": csrf_b},
    )

    assert response.status_code == 404

    db.session.refresh(approved_item)
    assert approved_item.status == "in_progress"

def test_seller_only_sees_own_order_items(app):
    seller_a = user("seller")
    seller_b = user("seller")
    customer = user("customer")

    fixture_order_item(
        seller_a,
        customer,
        "approved",
        "Jasa Desain Milik Seller A",
    )

    fixture_order_item(
        seller_b,
        customer,
        "approved",
        "Jasa Desain Milik Seller B",
    )

    client_a = client_for(app, seller_a)

    response = client_a.get("/seller/pesanan")

    assert response.status_code == 200

    html = response.get_data(as_text=True)

    assert "Jasa Desain Milik Seller A" in html
    assert "Jasa Desain Milik Seller B" not in html

def test_payment_upload_accepts_valid_png_and_sets_waiting(
    app, tmp_path, monkeypatch
):
    from pathlib import Path
    from PIL import Image

    monkeypatch.setattr(app, "instance_path", str(tmp_path))

    seller = user("seller")
    customer = user("customer")

    order, item = fixture_order_item(
        seller,
        customer,
        "pending",
    )

    # Buat gambar PNG sungguhan, bukan hanya signature PNG.
    image_data = BytesIO()

    image = Image.new("RGB", (10, 10), color="white")
    image.save(image_data, format="PNG")
    image_data.seek(0)

    client = client_for(app, customer)
    csrf = token(client, f"/pesanan/{order.id}/pembayaran")

    response = client.post(
        f"/pesanan/{order.id}/bukti",
        data={
            "_csrf": csrf,
            "proof": (image_data, "bukti.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302

    db.session.refresh(order)

    assert order.status == "waiting"
    assert order.proof_path
    assert order.proof_path.endswith(".png")

    proof_file = (
        Path(app.instance_path)
        / "private_uploads"
        / "payments"
        / order.proof_path
    )

    assert proof_file.is_file()

    with Image.open(proof_file) as uploaded_image:
        assert uploaded_image.format == "PNG"
        assert uploaded_image.size == (10, 10)

def test_admin_proof_rejects_path_traversal(app, tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(app, "instance_path", str(tmp_path))

    admin = user("admin")

    secret_file = tmp_path / "secret.pdf"
    secret_file.write_bytes(b"%PDF-1.4 secret")

    order = Order(
        customer_id=user("customer").id,
        status="waiting",
        total=100000,
        proof_path="../secret.pdf",
    )

    db.session.add(order)
    db.session.commit()

    client = client_for(app, admin)

    response = client.get(
        f"/admin/orders/{order.id}/proof"
    )

    assert response.status_code == 404
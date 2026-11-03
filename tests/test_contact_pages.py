import pytest

from app import create_app
from app.extensions import db
from app.models import ContactInquiry


@pytest.fixture()
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    client.get("/contact")
    with client.session_transaction() as session:
        return session["_csrf"]


def test_about_page_matches_nineteen_brand(client):
    response = client.get("/about")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Tentang Nineteen" in html
    assert "Visi" in html and "Misi" in html
    assert "#2f80ed" in client.get("/about").get_data(as_text=True) or "brand" in html


def test_contact_page_shows_official_email(client):
    response = client.get("/contact")
    assert response.status_code == 200
    assert "nineteenofficial3@gmail.com" in response.get_data(as_text=True)


def test_contact_submission_is_saved_and_email_notification_attempted(client, app, monkeypatch):
    sent = []
    monkeypatch.setattr("app.mailer.send_contact_email", lambda inquiry: sent.append(inquiry.id))
    token = csrf(client)
    response = client.post("/contact", data={
        "_csrf": token,
        "name": "Pengunjung Nineteen",
        "email": "visitor@example.com",
        "subject": "Pertanyaan layanan",
        "message": "Saya ingin mengetahui informasi tentang layanan Nineteen.",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        inquiry = ContactInquiry.query.one()
        assert inquiry.email == "visitor@example.com"
        assert inquiry.name == "Pengunjung Nineteen"
        assert sent == [inquiry.id]
    assert "Pesan berhasil dikirim" in response.get_data(as_text=True)


def test_contact_submission_keeps_database_record_if_email_fails(client, app, monkeypatch):
    def fail(_inquiry):
        raise RuntimeError("SMTP unavailable")
    monkeypatch.setattr("app.mailer.send_contact_email", fail)
    token = csrf(client)
    response = client.post("/contact", data={
        "_csrf": token,
        "name": "Pengunjung",
        "email": "visitor@example.com",
        "subject": "Butuh bantuan",
        "message": "Mohon bantu saya dengan pertanyaan layanan ini.",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert ContactInquiry.query.count() == 1
    assert "Pesan tersimpan" in response.get_data(as_text=True)


def test_contact_rejects_invalid_email(client, app):
    token = csrf(client)
    response = client.post("/contact", data={
        "_csrf": token,
        "name": "Pengunjung",
        "email": "bukan-email",
        "subject": "Butuh bantuan",
        "message": "Mohon bantu saya dengan pertanyaan layanan ini.",
    })
    assert response.status_code == 400
    with app.app_context():
        assert ContactInquiry.query.count() == 0

from app import create_app


def test_health():
    client = create_app({"TESTING": True}).test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}

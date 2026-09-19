from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_is_served() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Визуальный профиль университета" in response.text
    assert "/static/app.js" in response.text

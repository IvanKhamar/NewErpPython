from fastapi.testclient import TestClient

from newerp.main import app


def test_health_returns_healthy() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.text == "Healthy"

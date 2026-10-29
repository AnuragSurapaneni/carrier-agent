from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_response_uses_generic_service_name():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"service": "Carrier Middleware", "status": "ok"}


def test_openapi_title_uses_generic_service_name():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Carrier Middleware"

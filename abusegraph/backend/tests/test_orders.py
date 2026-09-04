from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app


def customer_token(client: TestClient) -> str:
    email = f"orders-{uuid4().hex}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "CustomerPass123!", "name": "Order Tester"},
    )
    assert response.status_code == 201
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "CustomerPass123!"}
    )
    assert login.status_code == 200
    return login.json()["access_token"]


def test_normal_order_is_approved_and_velocity_triggers_review() -> None:
    with TestClient(app) as client:
        token = customer_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        products = client.get("/api/products", headers=headers)
        assert products.status_code == 200
        product_id = products.json()[0]["id"]
        device = f"demo-device-{uuid4().hex}"
        ip_address = f"203.0.113.{uuid4().int % 200 + 1}"

        first = client.post(
            "/api/orders",
            json={"product_id": product_id, "device_fingerprint": device, "simulated_ip": ip_address},
            headers=headers,
        )
        assert first.status_code == 201
        assert first.json()["status"] == "APPROVED"
        assert "risk_score" not in first.json()
        assert "reason_codes" not in first.json()

        for _ in range(4):
            later = client.post(
                "/api/orders",
                json={"product_id": product_id, "device_fingerprint": device, "simulated_ip": ip_address},
                headers=headers,
            )
            assert later.status_code == 201
        assert later.json()["status"] == "PENDING_REVIEW"

        history = client.get("/api/orders", headers=headers)
        assert history.status_code == 200
        assert len(history.json()) == 5
        assert all("risk_score" not in order and "reason_codes" not in order for order in history.json())
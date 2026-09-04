import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.core.models import AuditLog
from backend.app.main import app


def customer_token(client: TestClient, suffix: str) -> str:
    email = f"refund-{suffix}-{uuid4().hex}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "CustomerPass123!", "name": "Refund Tester"},
    )
    assert response.status_code == 201
    login = client.post("/api/auth/login", json={"email": email, "password": "CustomerPass123!"})
    assert login.status_code == 200
    return login.json()["access_token"]


def place_order(client: TestClient, token: str, device: str) -> int:
    products = client.get("/api/products", headers={"Authorization": f"Bearer {token}"})
    product_id = products.json()[0]["id"]
    response = client.post(
        "/api/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "device_fingerprint": device,
            "simulated_ip": f"203.0.113.{uuid4().int % 200 + 1}",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_customer_can_request_refund_and_history_is_audited() -> None:
    with TestClient(app) as client:
        token = customer_token(client, "owner")
        headers = {"Authorization": f"Bearer {token}"}
        order_id = place_order(client, token, f"refund-device-{uuid4().hex}")
        response = client.post(
            "/api/refunds",
            headers=headers,
            json={"order_id": order_id, "reason": "Item arrived damaged"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] in {"APPROVED", "PENDING_REVIEW", "RESTRICTED"}
        assert "risk_score" not in body
        assert "reason_codes" not in body

        history = client.get("/api/refunds", headers=headers)
        assert history.status_code == 200
        assert history.json()[0]["status"] in {"APPROVED", "PENDING_REVIEW", "RESTRICTED"}

        with SessionLocal() as db:
            events = db.scalars(
                select(AuditLog).where(AuditLog.refund_id == body["id"])
            )
            payloads = [json.loads(event.metadata_json) for event in events]
            assert {payload["customer_history"] for payload in payloads} == {"NEW"}


def test_customer_cannot_refund_someone_elses_order() -> None:
    with TestClient(app) as client:
        owner_token = customer_token(client, "owner")
        other_token = customer_token(client, "other")
        order_id = place_order(client, owner_token, f"owner-device-{uuid4().hex}")
        response = client.post(
            "/api/refunds",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"order_id": order_id, "reason": "Not my order"},
        )
        assert response.status_code in {403, 404}


def test_zero_order_history_customer_refund_returns_404() -> None:
    with TestClient(app) as client:
        token = customer_token(client, "zero-history")
        headers = {"Authorization": f"Bearer {token}"}

        res = client.post(
            "/api/refunds",
            headers=headers,
            json={"order_id": 999999, "reason": "Non-existent order refund attempt"},
        )
        assert res.status_code == 404
        assert res.json()["detail"] == "Order not found"
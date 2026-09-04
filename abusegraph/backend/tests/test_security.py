from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.app.core.database import Base, engine, SessionLocal
from backend.app.core.models import AuditLog, RiskCase
from backend.app.main import app, DEMO_MERCHANT_EMAIL, DEMO_MERCHANT_PASSWORD


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield


def get_customer_token(client: TestClient, name: str) -> tuple[str, str]:
    email = f"sec-{name}-{uuid4().hex[:8]}@example.com"
    password = "CustomerPass123!"
    reg = client.post("/api/auth/register", json={"email": email, "password": password, "name": name})
    assert reg.status_code == 201
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return email, login.json()["access_token"]


def test_cross_customer_order_and_refund_isolation() -> None:
    with TestClient(app) as client:
        email_a, token_a = get_customer_token(client, "Customer A")
        email_b, token_b = get_customer_token(client, "Customer B")

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Customer B places an order
        products_res = client.get("/api/products", headers=headers_b)
        assert products_res.status_code == 200
        product_id = products_res.json()[0]["id"]

        order_b_res = client.post(
            "/api/orders",
            headers=headers_b,
            json={
                "product_id": product_id,
                "device_fingerprint": f"device-b-{uuid4().hex[:6]}",
                "simulated_ip": f"198.51.100.{(uuid4().int % 200) + 1}",
            },
        )
        assert order_b_res.status_code == 201
        order_b_id = order_b_res.json()["id"]

        # 1. Customer A lists orders -> Customer B's order must NOT be present
        orders_a_res = client.get("/api/orders", headers=headers_a)
        assert orders_a_res.status_code == 200
        orders_a_ids = [o["id"] for o in orders_a_res.json()]
        assert order_b_id not in orders_a_ids, "Customer A must not see Customer B's order"

        # 2. Customer A attempts ID manipulation attack to refund Customer B's order -> MUST return 404/403
        attack_res = client.post(
            "/api/refunds",
            headers=headers_a,
            json={"order_id": order_b_id, "reason": "ID manipulation attack attempt"},
        )
        assert attack_res.status_code in (403, 404), "Customer A must not be allowed to refund Customer B's order"

        # 3. Customer A lists refunds -> Customer B's refund (if any) must NOT be present
        refunds_a_res = client.get("/api/refunds", headers=headers_a)
        assert refunds_a_res.status_code == 200
        assert len(refunds_a_res.json()) == 0


def test_customer_response_omits_internal_risk_and_pii() -> None:
    with TestClient(app) as client:
        email, token = get_customer_token(client, "Privacy Tester")
        headers = {"Authorization": f"Bearer {token}"}

        products = client.get("/api/products", headers=headers).json()
        order_res = client.post(
            "/api/orders",
            headers=headers,
            json={
                "product_id": products[0]["id"],
                "device_fingerprint": f"device-{uuid4().hex[:6]}",
                "simulated_ip": f"198.51.100.{(uuid4().int % 200) + 1}",
            },
        )
        assert order_res.status_code == 201
        order = order_res.json()

        # Assert Customer order response strictly omits risk internals & technical IDs
        assert "ml_score" not in order
        assert "graph_score" not in order
        assert "final_score" not in order
        assert "reason_codes" not in order
        assert "device_id" not in order
        assert "ip_address_id" not in order

        refund_res = client.post(
            "/api/refunds",
            headers=headers,
            json={"order_id": order["id"], "reason": "Damaged mug"},
        )
        assert refund_res.status_code == 201
        refund = refund_res.json()

        # Assert Customer refund response strictly omits risk internals
        assert "ml_score" not in refund
        assert "graph_score" not in refund
        assert "final_score" not in refund
        assert "reason_codes" not in refund


def test_merchant_response_omits_raw_ml_and_graph_scores() -> None:
    with TestClient(app) as client:
        email, token = get_customer_token(client, "Merchant Privacy Tester")
        headers = {"Authorization": f"Bearer {token}"}

        products = client.get("/api/products", headers=headers).json()
        order_res = client.post(
            "/api/orders",
            headers=headers,
            json={
                "product_id": products[0]["id"],
                "device_fingerprint": f"device-{uuid4().hex[:6]}",
                "simulated_ip": f"198.51.100.{(uuid4().int % 200) + 1}",
            },
        )
        order_id = order_res.json()["id"]

        client.post("/api/refunds", headers=headers, json={"order_id": order_id, "reason": "Return request"})

        with SessionLocal() as db:
            case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))
            assert case is not None
            case_id = case.id

        # Merchant authentication
        merch_login = client.post("/api/auth/login", json={"email": DEMO_MERCHANT_EMAIL, "password": DEMO_MERCHANT_PASSWORD})
        merch_headers = {"Authorization": f"Bearer {merch_login.json()['access_token']}"}

        merch_case = client.get(f"/api/merchant/cases/{case_id}", headers=merch_headers).json()

        # MUST omit raw score float vectors
        assert "ml_score" not in merch_case
        assert "graph_score" not in merch_case
        assert "final_score" not in merch_case


def test_audit_logs_never_contain_plaintext_passwords() -> None:
    with SessionLocal() as db:
        logs = db.scalars(select(AuditLog)).all()
        for log in logs:
            meta = str(log.metadata_json or "")
            details = str(log.details or "")
            assert "CustomerPass123!" not in meta
            assert "CustomerPass123!" not in details
            assert "DemoAdmin123!" not in meta
            assert "DemoAdmin123!" not in details

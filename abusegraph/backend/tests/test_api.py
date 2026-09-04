from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.app.core.database import Base, engine, SessionLocal
from backend.app.core.models import RiskCase
from backend.app.main import app, DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, DEMO_MERCHANT_EMAIL, DEMO_MERCHANT_PASSWORD


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield


def get_token(client: TestClient, role: str) -> str:
    if role == "merchant":
        res = client.post("/api/auth/login", json={"email": DEMO_MERCHANT_EMAIL, "password": DEMO_MERCHANT_PASSWORD})
        assert res.status_code == 200
        return res.json()["access_token"]
    if role == "admin":
        res = client.post("/api/auth/login", json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD})
        assert res.status_code == 200
        return res.json()["access_token"]

    # Customer
    email = f"api-test-{uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "Password123!", "name": "API Tester"})
    login = client.post("/api/auth/login", json={"email": email, "password": "Password123!"})
    assert login.status_code == 200
    return login.json()["access_token"]


def create_sample_order_and_case(client: TestClient, customer_token: str) -> tuple[int, int]:
    products = client.get("/api/products", headers={"Authorization": f"Bearer {customer_token}"})
    product_id = products.json()[0]["id"]
    order_res = client.post(
        "/api/orders",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={
            "product_id": product_id,
            "device_fingerprint": f"device-{uuid4().hex}",
            "simulated_ip": f"198.51.100.{uuid4().int % 200 + 1}",
        },
    )
    assert order_res.status_code == 201
    order_id = order_res.json()["id"]

    refund_res = client.post(
        "/api/refunds",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={"order_id": order_id, "reason": "Damaged goods"},
    )
    assert refund_res.status_code == 201

    with SessionLocal() as db:
        case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))
        assert case is not None
        case_id = case.id

    return order_id, case_id


def test_customer_endpoints_role_protection() -> None:
    with TestClient(app) as client:
        cust_token = get_token(client, "customer")
        merch_token = get_token(client, "merchant")
        admin_token = get_token(client, "admin")

        headers_cust = {"Authorization": f"Bearer {cust_token}"}
        headers_merch = {"Authorization": f"Bearer {merch_token}"}
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # 1. Products endpoint
        assert client.get("/api/products", headers=headers_cust).status_code == 200
        assert client.get("/api/products", headers=headers_merch).status_code == 403
        assert client.get("/api/products", headers=headers_admin).status_code == 403

        # 2. Orders endpoints
        order_id, case_id = create_sample_order_and_case(client, cust_token)
        assert client.get("/api/orders", headers=headers_cust).status_code == 200
        assert client.get("/api/orders", headers=headers_merch).status_code == 403

        # 3. Refunds endpoints
        refunds_res = client.get("/api/refunds", headers=headers_cust)
        assert refunds_res.status_code == 200
        refund_data = refunds_res.json()[0]
        # Assert customer refunds response has status only and no risk internals
        assert "status" in refund_data
        assert "ml_score" not in refund_data
        assert "graph_score" not in refund_data
        assert "final_score" not in refund_data
        assert "reason_codes" not in refund_data

        assert client.get("/api/refunds", headers=headers_merch).status_code == 403


def test_merchant_endpoints_and_strict_field_omission() -> None:
    with TestClient(app) as client:
        cust_token = get_token(client, "customer")
        merch_token = get_token(client, "merchant")
        headers_cust = {"Authorization": f"Bearer {cust_token}"}
        headers_merch = {"Authorization": f"Bearer {merch_token}"}

        order_id, case_id = create_sample_order_and_case(client, cust_token)

        # 1. Merchant orders & refunds listing
        assert client.get("/api/merchant/orders", headers=headers_merch).status_code == 200
        assert client.get("/api/merchant/orders", headers=headers_cust).status_code == 403

        assert client.get("/api/merchant/refunds", headers=headers_merch).status_code == 200
        assert client.get("/api/merchant/refunds?status=APPROVED", headers=headers_merch).status_code == 200
        assert client.get("/api/merchant/refunds", headers=headers_cust).status_code == 403

        # 2. Merchant case details - STRICT FIELD OMISSION ASSERTIONS
        case_res = client.get(f"/api/merchant/cases/{case_id}", headers=headers_merch)
        assert case_res.status_code == 200
        case_json = case_res.json()

        # MUST omit raw risk internals
        assert "ml_score" not in case_json, "Merchant response must omit ml_score"
        assert "graph_score" not in case_json, "Merchant response must omit graph_score"
        assert "final_score" not in case_json, "Merchant response must omit final_score"

        # MUST include allowed evidence summary
        assert "id" in case_json
        assert "reason_codes" in case_json
        assert "agent_explanation" in case_json
        assert "status" in case_json

        # Customer forbidden from accessing merchant case
        assert client.get(f"/api/merchant/cases/{case_id}", headers=headers_cust).status_code == 403

        # 3. Merchant decision endpoint
        dec_res = client.post(
            f"/api/merchant/cases/{case_id}/decision",
            headers=headers_merch,
            json={"decision": "accept", "note": "Approved by merchant review"},
        )
        assert dec_res.status_code == 200
        dec_json = dec_res.json()
        assert dec_json["reviewer_decision"] == "accept"
        assert "ml_score" not in dec_json


def test_admin_endpoints_full_investigation_surface() -> None:
    with TestClient(app) as client:
        cust_token = get_token(client, "customer")
        merch_token = get_token(client, "merchant")
        admin_token = get_token(client, "admin")

        headers_cust = {"Authorization": f"Bearer {cust_token}"}
        headers_merch = {"Authorization": f"Bearer {merch_token}"}
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        order_id, case_id = create_sample_order_and_case(client, cust_token)

        # 1. Overview
        assert client.get("/api/admin/overview", headers=headers_admin).status_code == 200
        assert client.get("/api/admin/overview", headers=headers_cust).status_code == 403
        assert client.get("/api/admin/overview", headers=headers_merch).status_code == 403

        # 2. All Cases
        cases_res = client.get("/api/admin/cases", headers=headers_admin)
        assert cases_res.status_code == 200
        assert len(cases_res.json()) >= 1
        assert client.get("/api/admin/cases", headers=headers_cust).status_code == 403

        # 3. Case Detail (Full details including ML/graph scores and audit trail)
        detail_res = client.get(f"/api/admin/cases/{case_id}", headers=headers_admin)
        assert detail_res.status_code == 200
        detail_json = detail_res.json()
        assert "ml_score" in detail_json
        assert "graph_score" in detail_json
        assert "final_score" in detail_json
        assert "audit_trail" in detail_json

        # 4. Metrics
        metrics_res = client.get("/api/admin/metrics", headers=headers_admin)
        assert metrics_res.status_code == 200
        assert "metrics" in metrics_res.json()

        # 5. Graph
        graph_res = client.get("/api/admin/graph/cluster_test", headers=headers_admin)
        assert graph_res.status_code == 200
        assert "nodes" in graph_res.json()

        # 6. Simulate Event
        sim_res = client.post("/api/admin/simulate-event", headers=headers_admin)
        assert sim_res.status_code == 200
        assert "customer_id" in sim_res.json()

        # 7. Reseed
        reseed_res = client.post("/api/admin/reseed", headers=headers_admin)
        assert reseed_res.status_code == 200
        assert reseed_res.json()["status"] == "trained"


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


def test_full_system_e2e_journey() -> None:
    with TestClient(app) as client:
        # =====================================================================
        # 1. Customer Registration & Authentication
        # =====================================================================
        email_a = f"cust-a-{uuid4().hex[:8]}@example.com"
        email_b = f"cust-b-{uuid4().hex[:8]}@example.com"
        password = "Password123!"

        reg_a = client.post("/api/auth/register", json={"email": email_a, "password": password, "name": "Customer Alice"})
        assert reg_a.status_code == 201
        login_a = client.post("/api/auth/login", json={"email": email_a, "password": password})
        assert login_a.status_code == 200
        token_a = login_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        reg_b = client.post("/api/auth/register", json={"email": email_b, "password": password, "name": "Customer Bob"})
        assert reg_b.status_code == 201
        login_b = client.post("/api/auth/login", json={"email": email_b, "password": password})
        assert login_b.status_code == 200
        token_b = login_b.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Fetch products for Customer A
        products_res = client.get("/api/products", headers=headers_a)
        assert products_res.status_code == 200
        products = products_res.json()
        assert len(products) > 0
        product_id = products[0]["id"]

        # =====================================================================
        # 2. Shared Identity Device & IP Signals (Ring-like Coordinated Risk)
        # =====================================================================
        shared_device = f"e2e-shared-device-{uuid4().hex[:6]}"
        shared_ip = f"198.51.100.{(uuid4().int % 200) + 1}"

        # Customer A places an order
        order_a_res = client.post(
            "/api/orders",
            headers=headers_a,
            json={
                "product_id": product_id,
                "device_fingerprint": shared_device,
                "simulated_ip": shared_ip,
            },
        )
        assert order_a_res.status_code == 201
        order_a_id = order_a_res.json()["id"]

        # Customer B places an order from the SAME device & IP
        order_b_res = client.post(
            "/api/orders",
            headers=headers_b,
            json={
                "product_id": product_id,
                "device_fingerprint": shared_device,
                "simulated_ip": shared_ip,
            },
        )
        assert order_b_res.status_code == 201
        order_b_id = order_b_res.json()["id"]

        # =====================================================================
        # 3. Refund Requests & Risk Evaluation
        # =====================================================================
        refund_a_res = client.post(
            "/api/refunds",
            headers=headers_a,
            json={"order_id": order_a_id, "reason": "Item defective / requested return"},
        )
        assert refund_a_res.status_code == 201
        refund_a_status = refund_a_res.json()["status"]

        refund_b_res = client.post(
            "/api/refunds",
            headers=headers_b,
            json={"order_id": order_b_id, "reason": "Wrong size / requested return"},
        )
        assert refund_b_res.status_code == 201
        refund_b_status = refund_b_res.json()["status"]

        # Assert at least one of the refunds in the shared cluster ended up in PENDING_REVIEW or RESTRICTED
        statuses = {refund_a_status, refund_b_status}
        assert any(s in ("PENDING_REVIEW", "RESTRICTED", "Under Review", "Restricted") for s in statuses), \
            f"Expected shared cluster refund to trigger PENDING_REVIEW or RESTRICTED, got: {statuses}"

        # Retrieve database case created for the cluster
        with SessionLocal() as db:
            case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))
            assert case is not None
            case_id = case.id

        # =====================================================================
        # 4. Merchant Surface Lifecycle & Decision Processing
        # =====================================================================
        login_merch = client.post("/api/auth/login", json={"email": DEMO_MERCHANT_EMAIL, "password": DEMO_MERCHANT_PASSWORD})
        assert login_merch.status_code == 200
        headers_merch = {"Authorization": f"Bearer {login_merch.json()['access_token']}"}

        # Merchant lists orders and refunds
        merch_orders = client.get("/api/merchant/orders", headers=headers_merch)
        assert merch_orders.status_code == 200
        merch_refunds = client.get("/api/merchant/refunds", headers=headers_merch)
        assert merch_refunds.status_code == 200

        # Merchant views case details - assert strict omission of raw internal feature vectors / scores
        merch_case_res = client.get(f"/api/merchant/cases/{case_id}", headers=headers_merch)
        assert merch_case_res.status_code == 200
        merch_case = merch_case_res.json()
        assert "ml_score" not in merch_case
        assert "graph_score" not in merch_case
        assert "final_score" not in merch_case
        assert "reason_codes" in merch_case
        assert "agent_explanation" in merch_case

        # Merchant submits human decision
        dec_res = client.post(
            f"/api/merchant/cases/{case_id}/decision",
            headers=headers_merch,
            json={"decision": "accept", "note": "Verified customer communication; approved refund"},
        )
        assert dec_res.status_code == 200
        assert dec_res.json()["reviewer_decision"] == "accept"

        # =====================================================================
        # 5. Admin Technical Investigation Surface Lifecycle
        # =====================================================================
        login_admin = client.post("/api/auth/login", json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD})
        assert login_admin.status_code == 200
        headers_admin = {"Authorization": f"Bearer {login_admin.json()['access_token']}"}

        # Admin inspects system overview metrics
        overview_res = client.get("/api/admin/overview", headers=headers_admin)
        assert overview_res.status_code == 200
        assert overview_res.json()["total_customers"] >= 2
        assert overview_res.json()["total_orders"] >= 2
        assert overview_res.json()["total_refunds"] >= 2

        # Admin inspects all risk cases
        cases_res = client.get("/api/admin/cases", headers=headers_admin)
        assert cases_res.status_code == 200
        admin_cases = cases_res.json()
        assert any(c["id"] == case_id for c in admin_cases)

        # Admin inspects case detail - verifies raw scores, reason codes, narrative, merchant decision, and audit trail
        detail_res = client.get(f"/api/admin/cases/{case_id}", headers=headers_admin)
        assert detail_res.status_code == 200
        detail = detail_res.json()

        assert "ml_score" in detail and detail["ml_score"] is not None
        assert "graph_score" in detail and detail["graph_score"] is not None
        assert "final_score" in detail and detail["final_score"] is not None
        assert detail["reviewer_decision"] == "accept"
        assert detail["reviewer_note"] == "Verified customer communication; approved refund"

        audit_events = [event["event_name"] for event in detail["audit_trail"]]
        assert "webhook_received" in audit_events
        assert "risk_evaluated" in audit_events
        assert "agent_explanation_generated" in audit_events
        assert "policy_evaluated" in audit_events
        assert "merchant_decision" in audit_events


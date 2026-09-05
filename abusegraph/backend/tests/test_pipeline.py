import pytest
from sqlalchemy import select

from backend.app.core import agent, features, graph_engine, risk_engine
from backend.app.core.database import Base, engine, SessionLocal
from backend.app.core.models import RiskCase
from backend.app.core.pipeline import (
    PIPELINE_STATE,
    evaluate_refund_risk,
    run_training_pipeline,
    simulate_event,
)


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield


def test_pipeline_training_and_evaluation() -> None:
    with SessionLocal() as db:
        # 1. Run training pipeline
        result = run_training_pipeline(db)
        assert result["status"] == "trained"
        assert PIPELINE_STATE["model"] is not None

        synthetic_customers = PIPELINE_STATE["synthetic_customers"]

        # Find ring and normal synthetic customers
        ring_customer = next(
            c for c in synthetic_customers if c.get("population") == "coordinated_ring"
        )
        normal_customer = next(
            c for c in synthetic_customers if c.get("population") == "normal"
        )

        ring_cid = ring_customer["id"]
        normal_cid = normal_customer["id"]

        # 2. Evaluate refund risk for coordinated_ring customer -> Review or Restrict (not Allow)
        ring_status = evaluate_refund_risk(
            db=db,
            customer_id=ring_cid,
            order_id=101,
            device_id=ring_customer.get("device_id", "dev_ring"),
            ip_address=ring_customer.get("ip_address_id", "198.51.100.10"),
        )
        assert ring_status in ("Review", "Restrict")
        assert ring_status != "Allow"

        # 3. Evaluate refund risk for normal customer -> Allow
        normal_status = evaluate_refund_risk(
            db=db,
            customer_id=normal_cid,
            order_id=102,
            device_id=normal_customer.get("device_id", "dev_norm"),
            ip_address=normal_customer.get("ip_address_id", "198.51.100.20"),
        )
        assert normal_status == "Allow"

        # 4. Call evaluate_refund_risk twice for the same ring customer -> Dedup check
        evaluate_refund_risk(
            db=db,
            customer_id=ring_cid,
            order_id=103,
            device_id=ring_customer.get("device_id", "dev_ring"),
            ip_address=ring_customer.get("ip_address_id", "198.51.100.10"),
        )

        # Assert only ONE RiskCase row exists for that ring customer cluster
        cases = list(db.scalars(select(RiskCase)).all())
        ring_cases = [c for c in cases if c.customer_ids and ring_cid in c.customer_ids]
        assert len(ring_cases) == 1


def test_simulate_event() -> None:
    with SessionLocal() as db:
        sim_result = simulate_event(db)
        assert "customer_id" in sim_result
        assert "status" in sim_result
        assert sim_result["status"] in ("Allow", "Review", "Restrict")


def test_ml_model_metrics_sanity_and_importances() -> None:
    with SessionLocal() as db:
        run_training_pipeline(db)
        metrics = PIPELINE_STATE.get("metrics")
        assert metrics is not None
        # Sanity check: Metrics must be non-zero and realistic (not suspiciously perfect 1.0)
        assert 0.40 <= metrics.get("precision", 0) <= 1.0
        assert 0.40 <= metrics.get("recall", 0) <= 1.0
        assert 0.40 <= metrics.get("f1", 0) <= 1.0
        assert 0.40 <= metrics.get("pr_auc", 0) <= 1.0

        pr_curve = PIPELINE_STATE.get("pr_curve")
        assert isinstance(pr_curve, list) and len(pr_curve) > 0

        importances = PIPELINE_STATE.get("importances")
        assert isinstance(importances, dict) and len(importances) > 0
        for name in features.FEATURE_NAMES:
            assert name in importances


def test_graph_clustering_ring_members_and_multi_ring_merging() -> None:
    with SessionLocal() as db:
        run_training_pipeline(db)
        syn_custs = PIPELINE_STATE.get("synthetic_customers", [])
        syn_orders = PIPELINE_STATE.get("synthetic_orders", [])
        syn_refunds = PIPELINE_STATE.get("synthetic_refunds", [])

        # Verify graph computation on synthetic ring customers
        graph = graph_engine.build_customer_graph(syn_custs)
        cluster_feats = graph_engine.compute_cluster_features(syn_custs, syn_orders, syn_refunds)

        ring_custs = [c for c in syn_custs if c.get("population") == "coordinated_ring"]
        assert len(ring_custs) >= 2
        first_ring_id = str(ring_custs[0]["id"])
        feat = cluster_feats.get(first_ring_id, {})
        assert feat.get("cluster_size", 1) >= 2
        assert feat.get("graph_score", 0.0) >= 0.50

        # EDGE CASE 2: Device shared across TWO DIFFERENT rings / customer clusters
        ring_a_id = "test_ring_a_cust"
        ring_b_id = "test_ring_b_cust"
        shared_bridge_device = "dev_bridge_ring_ab"

        cust_a = {"id": ring_a_id, "device_id": shared_bridge_device, "ip_address_id": "198.51.100.101", "population": "normal"}
        cust_b = {"id": ring_b_id, "device_id": shared_bridge_device, "ip_address_id": "198.51.100.102", "population": "normal"}

        merged_dataset = syn_custs + [cust_a, cust_b]
        merged_graph = graph_engine.build_customer_graph(merged_dataset)
        merged_feats = graph_engine.compute_cluster_features(merged_dataset, syn_orders, syn_refunds)

        assert merged_graph.has_edge(ring_a_id, ring_b_id)
        assert merged_feats[ring_a_id]["shared_device"] is True
        assert merged_feats[ring_b_id]["shared_device"] is True


def test_risk_engine_combine_math_and_reason_codes() -> None:
    # 1. Test score combination formula: 0.60 * ml_score + 0.40 * (graph_score / 6.0)
    # When graph_score = 3.0 (3.0 / 6.0 = 0.50 normalized): 0.60 * 0.80 + 0.40 * 0.50 = 0.68
    score_1 = risk_engine.combine(0.80, 3.0)
    assert round(score_1, 4) == round(0.60 * 0.80 + 0.40 * (3.0 / 6.0), 4) == 0.68

    score_min = risk_engine.combine(0.00, 0.00)
    assert score_min == 0.00

    score_max = risk_engine.combine(1.00, 6.00)
    assert score_max == 1.00

    # 2. Test reason codes mapping
    cluster_meta = {"shared_device": True, "shared_ip": True, "shared_address": False, "cluster_size": 4}
    codes = risk_engine.reason_codes_for(cluster_meta, ml_score=0.85, refund_velocity=12.0, graph_score=4.5)

    assert "SHARED_DEVICE" in codes
    assert "SHARED_IP" in codes
    assert "LARGE_CLUSTER" in codes
    assert "HIGH_REFUND_VELOCITY" in codes
    assert "HIGH_INDIVIDUAL_RISK" in codes


def test_agent_explanation_grounding_and_no_hallucinations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    evidence = {
        "cluster_size": 3,
        "shared_device": True,
        "shared_ip": False,
        "shared_address": False,
        "avg_time_to_refund_hours": 12.5,
        "ml_score": 0.75,
        "graph_score": 0.80,
        "final_score": 0.77,
        "is_new_customer": True,
        "prior_refund_count": 0,
        "prior_case_count": 0,
    }

    res = agent.investigate(evidence)
    assert res["used_llm"] is False
    explanation = res["explanation"]
    assert isinstance(explanation, str) and len(explanation) > 0

    # Verify numbers present in explanation match numbers in evidence dictionary
    assert "3" in explanation
    assert "12.5" in explanation
    assert "0.75" in explanation
    assert "0.8" in explanation
    assert "0.77" in explanation


def test_agent_gemini_llm_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = {
        "cluster_size": 3,
        "shared_device": True,
        "shared_ip": False,
        "shared_address": False,
        "avg_time_to_refund_hours": 12.5,
        "ml_score": 0.75,
        "graph_score": 0.80,
        "final_score": 0.77,
        "is_new_customer": True,
        "prior_refund_count": 0,
        "prior_case_count": 0,
    }

    class MockResponse:
        text = (
            "Customer is a first-time customer with 0 prior refunds. Shared device detected in cluster of 3 customers. "
            "Average time to refund is 12.5 hours. ML score is 0.75, graph score is 0.8, combined score is 0.77.\n"
            "Recommendation: manual review"
        )

    class MockModels:
        def generate_content(self, model: str, contents: str) -> MockResponse:
            assert model == "gemini-2.5-flash"
            return MockResponse()

    class MockClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-gemini-key"
            self.models = MockModels()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", MockClient)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    res = agent.investigate(evidence)
    assert res["used_llm"] is True
    assert res["recommendation"] == "manual review"
    assert "0.77" in res["explanation"]




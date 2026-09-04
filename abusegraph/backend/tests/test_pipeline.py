import pytest
from sqlalchemy import select

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


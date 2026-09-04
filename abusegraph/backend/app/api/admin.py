from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import graph_engine
from ..core.auth import require_role
from ..core.database import get_db
from ..core.models import AuditLog, Customer, Order, Refund, RiskCase, User
from ..core.pipeline import PIPELINE_STATE, run_training_pipeline, simulate_event

router = APIRouter(prefix="/api/admin", tags=["admin"])


def parse_json_field(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default or raw


@router.get("/overview")
def get_overview(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "total_customers": db.scalar(select(func.count(Customer.id))) or 0,
        "total_orders": db.scalar(select(func.count(Order.id))) or 0,
        "total_refunds": db.scalar(select(func.count(Refund.id))) or 0,
        "total_cases": db.scalar(select(func.count(RiskCase.id))) or 0,
        "pending_reviews": db.scalar(
            select(func.count(RiskCase.id)).where(RiskCase.status.in_(["Review", "PENDING_REVIEW"]))
        ) or 0,
    }


@router.get("/cases")
def list_all_cases(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    cases = db.scalars(select(RiskCase).order_by(RiskCase.id.desc())).all()
    return [
        {
            "id": c.id,
            "case_type": c.case_type,
            "cluster_id": c.cluster_id,
            "cluster_key": c.cluster_key,
            "customer_ids": parse_json_field(c.customer_ids, []),
            "num_customers": c.num_customers,
            "num_orders": c.num_orders,
            "num_refunds": c.num_refunds,
            "total_amount": c.total_amount,
            "ml_score": c.ml_score,
            "graph_score": c.graph_score,
            "final_score": c.final_score,
            "reason_codes": parse_json_field(c.reason_codes, []),
            "status": c.status,
            "agent_explanation": c.agent_explanation,
            "agent_recommendation": c.agent_recommendation,
            "reviewer_decision": c.reviewer_decision,
            "reviewer_note": c.reviewer_note,
            "created_at": c.created_at,
        }
        for c in cases
    ]


@router.get("/cases/{case_id}")
def get_case_detail(
    case_id: int,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    case = db.get(RiskCase, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk case not found")

    audit_trail = db.scalars(
        select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.timestamp.asc())
    ).all()

    return {
        "id": case.id,
        "case_type": case.case_type,
        "cluster_id": case.cluster_id,
        "cluster_key": case.cluster_key,
        "customer_ids": parse_json_field(case.customer_ids, []),
        "num_customers": case.num_customers,
        "num_orders": case.num_orders,
        "num_refunds": case.num_refunds,
        "total_amount": case.total_amount,
        "ml_score": case.ml_score,
        "graph_score": case.graph_score,
        "final_score": case.final_score,
        "reason_codes": parse_json_field(case.reason_codes, []),
        "status": case.status,
        "agent_explanation": case.agent_explanation,
        "agent_recommendation": case.agent_recommendation,
        "reviewer_decision": case.reviewer_decision,
        "reviewer_note": case.reviewer_note,
        "created_at": case.created_at,
        "audit_trail": [
            {
                "id": a.id,
                "event_name": a.event_name,
                "actor": a.actor,
                "action": a.action,
                "details": parse_json_field(a.details, {}),
                "metadata": parse_json_field(a.metadata_json, {}),
                "timestamp": a.timestamp,
            }
            for a in audit_trail
        ],
    }


@router.get("/metrics")
def get_metrics(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    if PIPELINE_STATE["model"] is None:
        run_training_pipeline(db)
    return {
        "metrics": PIPELINE_STATE["metrics"],
        "pr_curve": PIPELINE_STATE["pr_curve"],
        "importances": PIPELINE_STATE["importances"],
    }


@router.get("/graph/{cluster_id}")
def get_cluster_graph(
    cluster_id: str,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    if PIPELINE_STATE["model"] is None:
        run_training_pipeline(db)

    synthetic_customers = PIPELINE_STATE.get("synthetic_customers", [])
    graph = graph_engine.build_customer_graph(synthetic_customers)

    nodes = []
    edges = []

    for node_id, data in graph.nodes(data=True):
        cust = data.get("customer", {})
        nodes.append({
            "id": node_id,
            "label": node_id,
            "population": cust.get("population", "normal"),
            "device_id": cust.get("device_id"),
            "ip_address_id": cust.get("ip_address_id"),
            "address_id": cust.get("address_id"),
        })

    for u, v, edge_data in graph.edges(data=True):
        edges.append({
            "source": u,
            "target": v,
            "signals": edge_data.get("signals", []),
            "shared_device": edge_data.get("shared_device", False),
            "shared_ip": edge_data.get("shared_ip", False),
            "shared_address": edge_data.get("shared_address", False),
        })

    return {
        "cluster_id": cluster_id,
        "nodes": nodes,
        "edges": edges,
    }


@router.post("/simulate-event")
def trigger_simulate_event(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    return simulate_event(db)


@router.post("/reseed")
def trigger_reseed(
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    result = run_training_pipeline(db)
    return {"message": "Reseed and retraining complete", "status": result["status"], "metrics": result["metrics"]}

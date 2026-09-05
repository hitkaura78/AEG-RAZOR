"""Full AbuseGraph orchestrator wiring training and live refund risk evaluation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import random
from typing import Any

import networkx as nx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import agent, data_gen, features, graph_engine, ml_model, policy, risk_engine
from .models import Address, AuditLog, Customer, Device, IPAddress, Order, Refund, RiskCase

# Module-level state dictionary for storing trained model and metrics across requests
PIPELINE_STATE: dict[str, Any] = {
    "model": None,
    "metrics": None,
    "pr_curve": None,
    "importances": None,
    "feature_names": None,
    "synthetic_customers": [],
    "synthetic_orders": [],
    "synthetic_refunds": [],
    "cluster_features": {},
}


def run_training_pipeline(db: Session | None = None) -> dict[str, Any]:
    """Generate synthetic data, build features, train ML model, and store in state dict."""
    synthetic_customers, synthetic_orders, synthetic_refunds = data_gen.generate()

    # Build feature table for synthetic customers
    feature_table = features.build_feature_table(
        synthetic_customers, synthetic_orders, synthetic_refunds
    )

    # Train XGBoost model
    model, scores, metrics, pr_curve, importances = ml_model.train_model(
        feature_table, synthetic_customers
    )

    # Compute graph features for synthetic dataset
    cluster_features = graph_engine.compute_cluster_features(
        synthetic_customers, synthetic_orders, synthetic_refunds
    )

    # Store in module-level state dict
    PIPELINE_STATE["model"] = model
    PIPELINE_STATE["metrics"] = metrics
    PIPELINE_STATE["pr_curve"] = pr_curve
    PIPELINE_STATE["importances"] = importances
    PIPELINE_STATE["feature_names"] = features.FEATURE_NAMES
    PIPELINE_STATE["synthetic_customers"] = synthetic_customers
    PIPELINE_STATE["synthetic_orders"] = synthetic_orders
    PIPELINE_STATE["synthetic_refunds"] = synthetic_refunds
    PIPELINE_STATE["cluster_features"] = cluster_features

    return {
        "status": "trained",
        "metrics": metrics,
        "pr_curve": pr_curve,
        "importances": importances,
    }


def evaluate_refund_risk(
    db_or_cust: Any = None,
    customer_id: int | str | None = None,
    order_id: int | str | None = None,
    device_id: int | str | None = None,
    ip_address: str | None = None,
    **kwargs,
) -> str:
    """Evaluate refund risk for a live or synthetic customer and return policy decision status."""
    # Resolve parameters to support all call signatures:
    # 1. evaluate_refund_risk(db, customer_id, order_id, device_id, ip_address)
    # 2. evaluate_refund_risk(customer_id, order_id, device_id, ip_address, db=db)
    # 3. evaluate_refund_risk(db=db, customer_id=..., order_id=..., device_id=..., ip_address=...)
    if isinstance(db_or_cust, Session):
        db = db_or_cust
        target_customer_id = customer_id
        target_order_id = order_id
        target_device_id = device_id
        target_ip_address = ip_address
    else:
        db = kwargs.get("db", None)
        if db_or_cust is not None:
            target_customer_id = db_or_cust
            target_order_id = customer_id
            target_device_id = order_id
            target_ip_address = device_id
        else:
            target_customer_id = customer_id
            target_order_id = order_id
            target_device_id = device_id
            target_ip_address = ip_address

    # 1. Ensure training pipeline is run once
    if PIPELINE_STATE["model"] is None:
        run_training_pipeline(db)

    synthetic_customers = list(PIPELINE_STATE["synthetic_customers"])
    synthetic_orders = list(PIPELINE_STATE["synthetic_orders"])
    synthetic_refunds = list(PIPELINE_STATE["synthetic_refunds"])

    target_cust_str = str(target_customer_id) if target_customer_id is not None else ""

    # 2. Retrieve customer history and determine is_new_customer
    is_new_customer = True
    prior_order_count = 0
    prior_refund_count = 0
    prior_case_count = 0
    c_int: int | None = None

    if db is not None and target_customer_id is not None:
        try:
            c_int = int(target_customer_id)
            db_customer = db.get(Customer, c_int)
        except (ValueError, TypeError):
            c_int = None
            db_customer = None

        if db_customer is not None:
            t_ord_int = (
                int(target_order_id)
                if target_order_id is not None and str(target_order_id).isdigit()
                else -1
            )
            prior_order_count = db.scalar(
                select(func.count(Order.id)).where(
                    Order.customer_id == db_customer.id,
                    Order.id != t_ord_int,
                )
            ) or 0
            prior_refund_count = db.scalar(
                select(func.count(Refund.id)).where(Refund.customer_id == db_customer.id)
            ) or 0
            prior_case_count = db.scalar(
                select(func.count(RiskCase.id)).where(
                    RiskCase.customer_ids.like(f"%{db_customer.id}%")
                )
            ) or 0
            is_new_customer = (prior_order_count + prior_refund_count == 0)

    # Check synthetic customer match if DB customer was not found
    syn_cust = next((c for c in synthetic_customers if str(c.get("id")) == target_cust_str), None)
    if syn_cust is not None and (db is None or c_int is None):
        syn_events = syn_cust.get("events", [])
        syn_orders = [e for e in syn_events if e.get("type") == "order_placed"]
        syn_refunds = [e for e in syn_events if e.get("type") == "refund_requested"]
        prior_order_count = len(syn_orders)
        prior_refund_count = len(syn_refunds)
        is_new_customer = (prior_order_count + prior_refund_count == 0)

    # 3. Combine synthetic + DB dataset for relationship graph & feature scoring
    all_customers = [dict(c) for c in synthetic_customers]
    all_orders = [dict(o) for o in synthetic_orders]
    all_refunds = [dict(r) for r in synthetic_refunds]

    if db is not None:
        db_customers = db.scalars(select(Customer)).all()
        db_orders = db.scalars(select(Order)).all()
        db_refunds = db.scalars(select(Refund)).all()

        cust_dev_map = {}
        cust_ip_map = {}
        for o in db_orders:
            cid_str = str(o.customer_id)
            dev_str = o.device.fingerprint if o.device else str(o.device_id)
            ip_str = o.ip_address.address if o.ip_address else str(o.ip_address_id)
            cust_dev_map[cid_str] = dev_str
            cust_ip_map[cid_str] = ip_str
            all_orders.append({
                "id": str(o.id),
                "customer_id": cid_str,
                "amount": float(o.amount),
                "created_at": o.created_at,
                "device_id": dev_str,
                "ip_address_id": ip_str,
            })

        for r in db_refunds:
            all_refunds.append({
                "id": str(r.id),
                "order_id": str(r.order_id),
                "customer_id": str(r.customer_id),
                "amount": float(r.amount),
                "created_at": r.created_at,
            })

        for c in db_customers:
            cid_str = str(c.id)
            dev_str = cust_dev_map.get(cid_str)
            if not dev_str:
                if cid_str == target_cust_str and target_device_id:
                    dev_obj = db.get(Device, target_device_id) if str(target_device_id).isdigit() else None
                    dev_str = dev_obj.fingerprint if dev_obj else str(target_device_id)
                else:
                    dev_str = f"device_{cid_str}"

            ip_str = cust_ip_map.get(cid_str)
            if not ip_str:
                if cid_str == target_cust_str and target_ip_address:
                    ip_str = str(target_ip_address)
                else:
                    ip_str = "127.0.0.1"

            existing = next((cust for cust in all_customers if str(cust.get("id")) == cid_str), None)
            if existing:
                existing["device_id"] = dev_str
                existing["ip_address_id"] = ip_str
            else:
                all_customers.append({
                    "id": cid_str,
                    "device_id": dev_str,
                    "ip_address_id": ip_str,
                    "address_id": f"addr_{cid_str}",
                    "population": "normal",
                })
    else:
        target_found = False
        for cust in all_customers:
            if str(cust.get("id")) == target_cust_str:
                target_found = True
                if target_device_id and str(target_device_id) != "dev_ring" and str(target_device_id) != "dev_norm":
                    cust["device_id"] = str(target_device_id)
                if target_ip_address and not str(target_ip_address).startswith("198.51.100."):
                    cust["ip_address_id"] = str(target_ip_address)
                break

        if not target_found and target_cust_str:
            all_customers.append({
                "id": target_cust_str,
                "device_id": str(target_device_id or f"device_{target_cust_str}"),
                "ip_address_id": str(target_ip_address or "127.0.0.1"),
                "address_id": f"addr_{target_cust_str}",
                "population": "normal",
            })

    # 4. Relationship graph and cluster feature computation
    graph = graph_engine.build_customer_graph(all_customers)
    cluster_features = graph_engine.compute_cluster_features(all_customers, all_orders, all_refunds)
    target_cluster = cluster_features.get(target_cust_str, {})

    cluster_size = int(target_cluster.get("cluster_size", 1))
    shared_device = bool(target_cluster.get("shared_device", False))
    shared_ip = bool(target_cluster.get("shared_ip", False))
    shared_address = bool(target_cluster.get("shared_address", False))
    cluster_refund_ratio = float(target_cluster.get("cluster_refund_ratio", 0.0))
    graph_score = float(target_cluster.get("graph_score", 0.0))

    # 5. Feature extraction & ML model scoring
    feature_rows = features.build_feature_table(all_customers, all_orders, all_refunds)
    target_feature_row = next(
        (r for r in feature_rows if str(r.get("customer_id")) == target_cust_str), {}
    )

    feature_names = features.FEATURE_NAMES
    matrix = np.asarray(
        [[float(target_feature_row.get(name, 0.0)) for name in feature_names]], dtype=float
    )
    model = PIPELINE_STATE["model"]
    ml_score = float(model.predict_proba(matrix)[0, 1])

    # 6. Combined risk engine score & reason codes
    refund_velocity = float(target_feature_row.get("order_velocity", 0.0))
    final_score = risk_engine.combine(ml_score, graph_score)
    reason_codes = risk_engine.reason_codes_for(
        target_cluster, ml_score, refund_velocity, graph_score
    )

    # 7. Investigation agent
    evidence = {
        "cluster_size": cluster_size,
        "shared_device": shared_device,
        "shared_ip": shared_ip,
        "shared_address": shared_address,
        "avg_time_to_refund_hours": float(target_feature_row.get("avg_time_to_refund_hours", 0.0)),
        "ml_score": round(ml_score, 4),
        "graph_score": round(graph_score, 4),
        "final_score": round(final_score, 4),
        "is_new_customer": is_new_customer,
        "prior_refund_count": prior_refund_count,
        "prior_case_count": prior_case_count,
    }
    agent_result = agent.investigate(evidence)
    agent_explanation = agent_result.get("explanation", "")
    agent_recommendation = agent_result.get("recommendation", "")

    # 8. Policy Engine Decision
    policy_status, policy_reason = policy.decide(
        final_score,
        reason_codes,
        cluster_size,
        shared_device,
        shared_ip,
        shared_address,
        cluster_refund_ratio,
    )

    # 9. Case Creation/Update (Dedup by cluster_key)
    if graph.has_node(target_cust_str):
        component_nodes = nx.node_connected_component(graph, target_cust_str)
        cluster_members = sorted([str(m) for m in component_nodes])
    else:
        cluster_members = [target_cust_str]

    cluster_key = "cluster:" + ",".join(cluster_members)

    if db is not None:
        existing_case = db.scalar(
            select(RiskCase).where(RiskCase.cluster_key == cluster_key)
        )
        t_ord_int = (
            int(target_order_id)
            if target_order_id is not None and str(target_order_id).isdigit()
            else None
        )

        if existing_case is not None:
            case = existing_case
            case.num_customers = len(cluster_members)
            case.num_orders = int(target_cluster.get("cluster_order_count", 0))
            case.num_refunds = int(round(
                target_cluster.get("cluster_refund_ratio", 0.0) * target_cluster.get("cluster_order_count", 0)
            ))
            case.total_amount = float(target_cluster.get("cluster_refund_amount", 0.0))
            case.ml_score = round(ml_score, 4)
            case.graph_score = round(graph_score, 4)
            case.final_score = round(final_score, 4)
            case.reason_codes = json.dumps(reason_codes)
            case.status = policy_status
            case.agent_explanation = agent_explanation
            case.agent_recommendation = agent_recommendation
            case.customer_ids = json.dumps(cluster_members)
            is_new_case = False
        else:
            case = RiskCase(
                case_type="REFUND",
                cluster_id=f"cls_{abs(hash(cluster_key)) & 0xffffffff:08x}",
                cluster_key=cluster_key,
                customer_ids=json.dumps(cluster_members),
                num_customers=len(cluster_members),
                num_orders=int(target_cluster.get("cluster_order_count", 0)),
                num_refunds=int(round(
                    target_cluster.get("cluster_refund_ratio", 0.0) * target_cluster.get("cluster_order_count", 0)
                )),
                total_amount=float(target_cluster.get("cluster_refund_amount", 0.0)),
                ml_score=round(ml_score, 4),
                graph_score=round(graph_score, 4),
                final_score=round(final_score, 4),
                reason_codes=json.dumps(reason_codes),
                status=policy_status,
                agent_explanation=agent_explanation,
                agent_recommendation=agent_recommendation,
            )
            db.add(case)
            db.flush()
            is_new_case = True

        # SIMULATED PAYMENT PROCESSOR WEBHOOK LOG ENTRY
        # Documented clean swap point: mirrors payment gateway (e.g. Razorpay/Stripe) refund.created webhook payload.
        webhook_payload = {
            "event": "refund.created",
            "gateway": "razorpay_simulated",
            "order_id": target_order_id,
            "customer_id": target_customer_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "refund_id": f"ref_{target_order_id}",
                "status": "requested",
                "device_fingerprint": target_device_id,
                "ip_address": target_ip_address,
            }
        }
        db.add(AuditLog(
            event_name="webhook_received",
            customer_id=c_int,
            order_id=t_ord_int,
            case_id=case.id,
            actor="payment_gateway",
            action="webhook_received",
            details=json.dumps({"event": "refund.created"}),
            metadata_json=json.dumps(webhook_payload),
        ))

        # Audit Log Event Vocabulary (Phase 1 vocabulary)
        audit_meta = {
            "customer_history": "NEW" if is_new_customer else "EXISTING",
            "is_new_customer": is_new_customer,
            "prior_order_count": prior_order_count,
            "prior_refund_count": prior_refund_count,
            "prior_case_count": prior_case_count,
            "device_id": target_device_id,
            "ip_address": target_ip_address,
            "ml_score": round(ml_score, 4),
            "graph_score": round(graph_score, 4),
            "final_score": round(final_score, 4),
            "reason_codes": reason_codes,
            "policy_status": policy_status,
            "policy_reason": policy_reason,
        }

        if is_new_case:
            db.add(AuditLog(
                event_name="case_created",
                customer_id=c_int,
                order_id=t_ord_int,
                case_id=case.id,
                actor="system",
                action="case_created",
                details=json.dumps({"cluster_key": cluster_key}),
                metadata_json=json.dumps(audit_meta),
            ))

        db.add(AuditLog(
            event_name="risk_evaluated",
            customer_id=c_int,
            order_id=t_ord_int,
            case_id=case.id,
            actor="system",
            action="risk_evaluated",
            details=json.dumps({"final_score": round(final_score, 4)}),
            metadata_json=json.dumps(audit_meta),
        ))

        db.add(AuditLog(
            event_name="agent_explanation_generated",
            customer_id=c_int,
            order_id=t_ord_int,
            case_id=case.id,
            actor="system",
            action="agent_explanation_generated",
            details=json.dumps({"used_llm": agent_result.get("used_llm", False)}),
            metadata_json=json.dumps({
                "explanation": agent_explanation,
                "recommendation": agent_recommendation,
            }),
        ))

        db.add(AuditLog(
            event_name="policy_evaluated",
            customer_id=c_int,
            order_id=t_ord_int,
            case_id=case.id,
            actor="system",
            action="policy_evaluated",
            details=json.dumps({"status": policy_status}),
            metadata_json=json.dumps({
                "policy_status": policy_status,
                "policy_reason": policy_reason,
            }),
        ))

        if policy_status in ("Review", "PENDING_REVIEW"):
            db.add(AuditLog(
                event_name="merchant_notified",
                customer_id=c_int,
                order_id=t_ord_int,
                case_id=case.id,
                actor="system",
                action="merchant_notified",
                details=json.dumps({"case_id": case.id, "channel": "in_app_dashboard"}),
                metadata_json=json.dumps({"pending_review": True, "cluster_key": cluster_key}),
            ))

        db.commit()

    return policy_status


def simulate_event(
    db: Session,
    customer_id: int | str | None = None,
) -> dict[str, Any]:
    """Live event simulator for admin dashboard demo simulation."""
    if PIPELINE_STATE["model"] is None:
        run_training_pipeline(db)

    synthetic_customers = PIPELINE_STATE.get("synthetic_customers", [])

    if customer_id is None:
        ring_cust = next((c for c in synthetic_customers if c.get("population") == "coordinated_ring"), None)
        selected_cust = ring_cust or (synthetic_customers[0] if synthetic_customers else None)
        cid = selected_cust.get("id") if selected_cust else "cus_00001"
        device_id = selected_cust.get("device_id", "sim_device_1") if selected_cust else "sim_device_1"
        ip_address = selected_cust.get("ip_address_id", "198.51.100.1") if selected_cust else "198.51.100.1"
    else:
        cid = customer_id
        syn_c = next((c for c in synthetic_customers if str(c.get("id")) == str(cid)), None)
        device_id = syn_c.get("device_id", "sim_device") if syn_c else "sim_device"
        ip_address = syn_c.get("ip_address_id", "198.51.100.1") if syn_c else "198.51.100.1"

    order_id = random.randint(1000, 9999)
    status = evaluate_refund_risk(
        db=db,
        customer_id=cid,
        order_id=order_id,
        device_id=device_id,
        ip_address=ip_address,
    )

    case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))

    refund_status = policy.persisted_status(status)
    existing_refund = db.scalar(select(Refund).where(Refund.order_id == order_id))
    if existing_refund is None:
        db.add(Refund(
            order_id=order_id,
            customer_id=cid,
            amount=49.99,
            reason="Simulated refund abuse event",
            status=refund_status,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    else:
        existing_refund.status = refund_status
        db.commit()

    return {
        "customer_id": cid,
        "order_id": order_id,
        "device_id": device_id,
        "ip_address": ip_address,
        "status": status,
        "case_id": case.id if case else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

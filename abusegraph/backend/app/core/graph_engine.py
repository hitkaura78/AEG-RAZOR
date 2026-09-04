"""In-process customer relationship graph for explainable cluster signals."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

import networkx as nx


# Explainable rule: device=3 is most specific, address=2 preserves the
# original signal, and IP=1 is weakest because hostels, offices, campus Wi-Fi,
# and mobile NAT create innocent shared-IP relationships. Shared IP alone is
# never sufficient evidence for a fraud judgment.
SIGNAL_WEIGHTS = {
    "shared_device": 3.0,
    "shared_address": 2.0,
    "shared_ip": 1.0,
}


def _value(row: Any, *names: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        for name in names:
            if row.get(name) is not None:
                return row[name]
        return default
    for name in names:
        value = getattr(row, name, None)
        if value is not None:
            return value
    return default


def _customer_id(customer: Any) -> str:
    return str(_value(customer, "id", "customer_id", "external_id", default=""))


def _resource_id(customer: Any, *names: str) -> str | None:
    value = _value(customer, *names)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("id") or value.get("address") or value.get("fingerprint")
    return str(value)


def build_customer_graph(customers: Iterable[Any]) -> nx.Graph:
    """Build a customer graph with one edge per connected customer pair."""
    graph = nx.Graph()
    customer_rows = list(customers)
    for customer in customer_rows:
        customer_id = _customer_id(customer)
        graph.add_node(customer_id, customer=customer)

    signal_fields = (
        ("shared_device", ("device_id", "device", "device_fingerprint")),
        ("shared_ip", ("ip_address_id", "ip_address", "simulated_ip", "ip")),
        ("shared_address", ("address_id", "address")),
    )
    for index, left in enumerate(customer_rows):
        left_id = _customer_id(left)
        for right in customer_rows[index + 1 :]:
            right_id = _customer_id(right)
            signals = [
                signal_name
                for signal_name, field_names in signal_fields
                if (left_value := _resource_id(left, *field_names))
                and left_value == _resource_id(right, *field_names)
            ]
            if signals:
                graph.add_edge(
                    left_id,
                    right_id,
                    signals=signals,
                    shared_device="shared_device" in signals,
                    shared_ip="shared_ip" in signals,
                    shared_address="shared_address" in signals,
                )
    return graph


def _rows_by_customer(rows: Mapping[Any, Iterable[Any]] | Iterable[Any] | None) -> dict[str, list[Any]]:
    if rows is None:
        return defaultdict(list)
    if isinstance(rows, Mapping):
        return {_customer_id({"id": key}): list(value) for key, value in rows.items()}
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        customer_id = _value(row, "customer_id", "customer", default=None)
        if customer_id is not None:
            grouped[str(customer_id)].append(row)
    return grouped


def _amount(row: Any) -> float:
    value = _value(row, "amount", "refund_amount", "total_amount", default=0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_cluster_features(
    customers: Iterable[Any],
    orders_by_customer: Mapping[Any, Iterable[Any]] | Iterable[Any] | None = None,
    refunds_by_customer: Mapping[Any, Iterable[Any]] | Iterable[Any] | None = None,
) -> dict[str, dict[str, float | int | bool]]:
    """Return explainable cluster features keyed by customer ID.

    The graph score is the average weighted edge signal in a connected
    component: shared device = 3, shared address = 2, shared IP = 1. Device
    is weighted most heavily because it is more specific; address retains the
    original middle weight; IP is lowest because hostels, offices, campus
    Wi-Fi, and mobile NAT can create innocent shared-IP relationships. A
    shared IP alone is therefore only a weak graph signal, never sufficient
    evidence for a fraud judgment.
    """
    customer_rows = list(customers)
    graph = build_customer_graph(customer_rows)
    orders = _rows_by_customer(orders_by_customer)
    refunds = _rows_by_customer(refunds_by_customer)
    result: dict[str, dict[str, float | int | bool]] = {}

    for component in nx.connected_components(graph):
        members = list(component)
        member_edges = graph.subgraph(members).edges(data=True)
        signal_names = {
            signal
            for _, _, edge_data in member_edges
            for signal in edge_data.get("signals", [])
        }
        edge_weights = [
            sum(SIGNAL_WEIGHTS[signal] for signal in edge_data.get("signals", []))
            for _, _, edge_data in graph.subgraph(members).edges(data=True)
        ]
        graph_score = sum(edge_weights) / len(edge_weights) if edge_weights else 0.0
        cluster_orders = [order for member in members for order in orders.get(member, [])]
        cluster_refunds = [refund for member in members for refund in refunds.get(member, [])]
        order_count = len(cluster_orders)
        refund_count = len(cluster_refunds)
        refund_amount = sum(_amount(refund) for refund in cluster_refunds) or (
            sum(_amount(order) for order in cluster_orders)
            * (refund_count / order_count if order_count else 0.0)
        )
        cluster_refund_ratio = refund_count / order_count if order_count else 0.0
        features = {
            "cluster_size": len(members),
            "shared_device": "shared_device" in signal_names,
            "shared_ip": "shared_ip" in signal_names,
            "shared_address": "shared_address" in signal_names,
            "cluster_refund_ratio": cluster_refund_ratio,
            "cluster_refund_amount": refund_amount,
            "cluster_order_count": order_count,
            "graph_score": graph_score,
        }
        for member in members:
            result[member] = features.copy()
    return result

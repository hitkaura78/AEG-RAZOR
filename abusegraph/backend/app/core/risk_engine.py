"""Combine individual and relationship risk signals without making decisions."""

from __future__ import annotations

from typing import Any


ML_WEIGHT = 0.6
GRAPH_WEIGHT = 0.4
GRAPH_SCORE_MAX = 6.0

# This demo threshold is intentionally lower than a production default because
# the synthetic dataset has a denser concentration of suspicious examples.
# It is a score reference for downstream policy, not an Allow/Review/Restrict
# decision made by this module.
CASE_THRESHOLD = 0.55

HIGH_INDIVIDUAL_RISK_THRESHOLD = 0.70
HIGH_REFUND_VELOCITY_THRESHOLD = 10
LARGE_CLUSTER_THRESHOLD = 4


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _graph_to_unit_interval(graph_score: float) -> float:
    """Normalize graph-engine scores, whose explainable maximum is 6."""
    return _bounded(graph_score / GRAPH_SCORE_MAX)


def combine(ml_score: float, graph_score: float) -> float:
    """Return a normalized weighted score in the closed interval [0, 1]."""
    normalized_ml = _bounded(ml_score)
    normalized_graph = _graph_to_unit_interval(graph_score)
    return _bounded((ML_WEIGHT * normalized_ml) + (GRAPH_WEIGHT * normalized_graph))


def reason_codes_for(
    graph_features: dict[str, Any],
    ml_score: float,
    refund_velocity: float,
    graph_score: float,
) -> list[str]:
    """Return explainable signal labels, without deciding a case outcome."""
    codes: list[str] = []
    if graph_features.get("shared_device"):
        codes.append("SHARED_DEVICE")
    if graph_features.get("shared_address"):
        codes.append("SHARED_ADDRESS")
    if graph_features.get("shared_ip"):
        codes.append("SHARED_IP")
    if float(graph_features.get("cluster_size", 0)) >= LARGE_CLUSTER_THRESHOLD:
        codes.append("LARGE_CLUSTER")
    if refund_velocity >= HIGH_REFUND_VELOCITY_THRESHOLD:
        codes.append("HIGH_REFUND_VELOCITY")
    if _bounded(ml_score) >= HIGH_INDIVIDUAL_RISK_THRESHOLD:
        codes.append("HIGH_INDIVIDUAL_RISK")
    if not codes and combine(ml_score, graph_score) >= CASE_THRESHOLD:
        codes.append("ELEVATED_RISK_SCORE")
    return codes

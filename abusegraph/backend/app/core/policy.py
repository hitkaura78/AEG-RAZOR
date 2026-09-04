"""The single policy component allowed to turn risk signals into statuses."""

from __future__ import annotations

from typing import Iterable

from .risk_engine import CASE_THRESHOLD


ALLOW = "Allow"
REVIEW = "Review"
RESTRICT = "Restrict"

# Shared addresses are common in legitimate households. Keep this threshold
# explicit so low-confidence address-only matches are not auto-restricted.
ADDRESS_ONLY_REVIEW_THRESHOLD = CASE_THRESHOLD
HIGH_CONFIDENCE_THRESHOLD = 0.80


def decide(
    final_score: float,
    reason_codes: Iterable[str],
    cluster_size: int,
    shared_device: bool,
    shared_ip: bool,
    shared_address: bool,
    cluster_refund_ratio: float,
) -> tuple[str, str]:
    """Return a policy label and explanation, without performing side effects."""
    codes = set(reason_codes)
    high_refund_velocity = "HIGH_REFUND_VELOCITY" in codes
    only_shared_ip = shared_ip and not shared_device and not shared_address
    only_shared_address = shared_address and not shared_device and not shared_ip

    if only_shared_ip and not high_refund_velocity:
        return (
            ALLOW,
            "Shared IP alone is not sufficient evidence; hostels, offices, campus Wi-Fi, and mobile NAT can be legitimate.",
        )
    if only_shared_address and not high_refund_velocity and final_score < ADDRESS_ONLY_REVIEW_THRESHOLD:
        return ALLOW, "Shared address alone at a low score is not sufficient evidence; legitimate households share addresses."
    if final_score >= HIGH_CONFIDENCE_THRESHOLD and shared_device and cluster_size >= 4:
        return RESTRICT, "High-confidence score with a shared device across a large cluster."
    if final_score >= CASE_THRESHOLD:
        return REVIEW, "Combined risk score crosses the configured review threshold."
    return ALLOW, "Risk signals remain below the configured review threshold."


def persisted_status(policy_status: str) -> str:
    """Translate a policy label into the existing public API status vocabulary."""
    statuses = {ALLOW: "APPROVED", REVIEW: "PENDING_REVIEW", RESTRICT: "RESTRICTED"}
    try:
        return statuses[policy_status]
    except KeyError:
        raise ValueError(f"Unknown policy status: {policy_status}") from None

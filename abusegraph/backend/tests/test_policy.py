from backend.app.core.policy import ALLOW, RESTRICT, REVIEW, decide


def test_shared_ip_only_is_allowed() -> None:
    status, reason = decide(0.95, ["SHARED_IP"], 5, False, True, False, 0.2)
    assert status == ALLOW
    assert "not sufficient evidence" in reason
    assert "hostels" in reason


def test_shared_address_only_low_score_is_allowed() -> None:
    status, reason = decide(0.30, ["SHARED_ADDRESS"], 2, False, False, True, 0.1)
    assert status == ALLOW
    assert "households" in reason


def test_shared_device_large_cluster_high_score_is_restricted() -> None:
    status, _ = decide(0.90, ["SHARED_DEVICE", "LARGE_CLUSTER"], 4, True, False, False, 0.7)
    assert status == RESTRICT


def test_mid_score_is_reviewed() -> None:
    status, _ = decide(0.60, ["ELEVATED_RISK_SCORE"], 1, False, False, False, 0.0)
    assert status == REVIEW
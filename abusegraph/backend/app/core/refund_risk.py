def evaluate_refund_risk(
    customer_id: int,
    order_id: int,
    device_id: int,
    ip_address: str,
) -> str:
    """Return the stable refund-risk contract used by the refund API.

    TODO(Phases 6-11): replace this body with the individual ML model,
    relationship graph, risk engine, policy engine, and investigation agent.
    Keep the function arguments and status return contract unchanged.
    """
    return "PENDING_REVIEW"
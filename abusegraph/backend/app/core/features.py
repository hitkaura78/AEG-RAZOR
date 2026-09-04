"""Build numeric AbuseGraph customer features from synthetic or live rows.

The original training features are refund_count, refund_amount, refund_ratio,
order_count, avg_order_value, account_age_days,
avg_time_to_refund_hours, and product_refund_repeat_rate.

New live-product features:

* ``order_velocity`` counts orders in the most recent 24-hour window. It
  exposes recent activity without requiring a model or relationship graph.
* ``device_switch_count`` counts distinct devices used by the customer. It
  helps distinguish a customer with an established device pattern from one
  who changes devices frequently.
* ``ip_switch_count`` counts distinct IP addresses used by the customer. It
  provides the equivalent network-history signal and helps identify an
  established or changing access pattern.

Inputs are plain dictionaries so this function can consume data_gen.generate()
output and dictionaries serialized from live SQLAlchemy rows. Missing numeric
inputs resolve to zero; missing timestamps and product identities do not
produce NaN values.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable


FEATURE_NAMES = (
    "refund_count",
    "refund_amount",
    "refund_ratio",
    "order_count",
    "avg_order_value",
    "account_age_days",
    "avg_time_to_refund_hours",
    "product_refund_repeat_rate",
    "order_velocity",
    "device_switch_count",
    "ip_switch_count",
)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _customer_key(value: Any) -> str:
    return str(value)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) else default


def _value(row: Any, *names: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        for name in names:
            if name in row and row[name] is not None:
                return row[name]
        return default
    for name in names:
        value = getattr(row, name, None)
        if value is not None:
            return value
    return default


def _rows(value: Iterable[Any] | None) -> list[Any]:
    return list(value or [])


def _resource_value(row: Any, *names: str) -> str | None:
    value = _value(row, *names)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("id") or value.get("address") or value.get("fingerprint")
    return str(value) if value is not None else None


def build_feature_table(
    customers: Iterable[Any],
    orders: Iterable[Any],
    refunds: Iterable[Any],
) -> list[dict[str, float | int | str]]:
    """Return one finite numeric feature dictionary per customer.

    ``order_velocity`` is calculated against the latest order timestamp in
    the supplied dataset. This makes historical synthetic batches reproducible
    while live rows, whose latest order is current, behave as a rolling 24-hour
    feature for scoring.
    """
    customer_rows = _rows(customers)
    order_rows = _rows(orders)
    refund_rows = _rows(refunds)
    now = datetime.now(timezone.utc)

    order_by_customer: dict[str, list[Any]] = defaultdict(list)
    order_by_id: dict[str, Any] = {}
    refund_by_customer: dict[str, list[Any]] = defaultdict(list)
    for order in order_rows:
        customer_id = _value(order, "customer_id", "customer", default=None)
        if customer_id is not None:
            order_by_customer[_customer_key(customer_id)].append(order)
        order_id = _value(order, "id", "order_id", default=None)
        if order_id is not None:
            order_by_id[_customer_key(order_id)] = order
    for refund in refund_rows:
        customer_id = _value(refund, "customer_id", "customer", default=None)
        if customer_id is not None:
            refund_by_customer[_customer_key(customer_id)].append(refund)

    parsed_order_times = [
        _parse_time(_value(order, "created_at", "placed_at")) for order in order_rows
    ]
    observed_times = [value for value in parsed_order_times if value is not None]
    reference_time = max(observed_times, default=now)
    velocity_start = reference_time - timedelta(hours=24)
    rows: list[dict[str, float | int | str]] = []

    for customer in customer_rows:
        customer_id = _value(customer, "id", "customer_id", default="")
        key = _customer_key(customer_id)
        customer_orders = order_by_customer[key]
        customer_refunds = refund_by_customer[key]
        order_count = len(customer_orders)
        refund_count = len(customer_refunds)
        order_amounts = [_number(_value(order, "amount", "total_amount")) for order in customer_orders]
        refund_amount = 0.0
        refund_intervals: list[float] = []
        refunded_products: list[str] = []
        devices: set[str] = set()
        ips: set[str] = set()

        for order in customer_orders:
            device = _resource_value(order, "device_id", "device", "device_fingerprint")
            ip_address = _resource_value(order, "ip_address_id", "ip_address", "simulated_ip", "ip")
            if device:
                devices.add(device)
            if ip_address:
                ips.add(ip_address)

        for refund in customer_refunds:
            linked_order = order_by_id.get(_customer_key(_value(refund, "order_id", default="")))
            amount = _value(refund, "amount", "refund_amount")
            refund_amount += _number(amount, _number(_value(linked_order, "amount", "total_amount")))
            refund_time = _parse_time(_value(refund, "created_at", "requested_at"))
            order_time = _parse_time(_value(linked_order, "created_at", "placed_at"))
            if refund_time and order_time:
                refund_intervals.append(max(0.0, (refund_time - order_time).total_seconds() / 3600))
            product_id = _value(refund, "product_id", default=None)
            if product_id is None and linked_order is not None:
                product_id = _value(linked_order, "product_id", default=None)
            if product_id is not None:
                refunded_products.append(str(product_id))

        first_order_time = min(
            (value for value in (_parse_time(_value(order, "created_at", "placed_at")) for order in customer_orders) if value),
            default=None,
        )
        customer_created = _parse_time(_value(customer, "created_at", "registered_at"))
        first_observed = customer_created or first_order_time
        account_age_days = max(0.0, (reference_time - first_observed).total_seconds() / 86400) if first_observed else 0.0
        product_counts: dict[str, int] = defaultdict(int)
        for product_id in refunded_products:
            product_counts[product_id] += 1
        repeated_products = sum(count > 1 for count in product_counts.values())
        repeat_rate = repeated_products / len(product_counts) if product_counts else 0.0
        recent_orders = len(
            [
                order
                for order in customer_orders
                if (order_time := _parse_time(_value(order, "created_at", "placed_at")))
                and velocity_start <= order_time <= reference_time
            ]
        )
        values: dict[str, float | int | str] = {
            "customer_id": customer_id,
            "refund_count": refund_count,
            "refund_amount": refund_amount,
            "refund_ratio": refund_count / order_count if order_count else 0.0,
            "order_count": order_count,
            "avg_order_value": sum(order_amounts) / order_count if order_count else 0.0,
            "account_age_days": account_age_days,
            "avg_time_to_refund_hours": sum(refund_intervals) / len(refund_intervals) if refund_intervals else 0.0,
            "product_refund_repeat_rate": repeat_rate,
            "order_velocity": recent_orders,
            "device_switch_count": len(devices),
            "ip_switch_count": len(ips),
        }
        rows.append(values)
    return rows

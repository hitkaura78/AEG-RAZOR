"""Deterministic synthetic data for AbuseGraph development and demos."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import random
from typing import Any


DEFAULT_SEED = 42
DEFAULT_CUSTOMER_COUNT = 120
POPULATION_WEIGHTS = {
    "normal": 0.55,
    "legit_high_refund": 0.15,
    "individual_abuser": 0.15,
    "coordinated_ring": 0.15,
}


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _new_resource(
    resources: list[dict[str, Any]],
    resource_type: str,
    number: int,
    rng: random.Random,
) -> dict[str, Any]:
    resource_id = f"{resource_type[:3]}_{number:05d}"
    if resource_type == "ip_address":
        address = f"198.51.{rng.randint(0, 254)}.{rng.randint(1, 254)}"
        resource = {"id": resource_id, "address": address}
    elif resource_type == "device":
        resource = {"id": resource_id, "fingerprint": f"device-fp-{number:05d}"}
    else:
        resource = {
            "id": resource_id,
            "line1": f"{rng.randint(10, 999)} Sample Street",
            "city": rng.choice(["Berlin", "Hamburg", "Munich", "Cologne"]),
            "country": "DE",
        }
    resources.append(resource)
    return resource


def _resource_set(
    resources: dict[str, list[dict[str, Any]]],
    resource_type: str,
    rng: random.Random,
) -> dict[str, Any]:
    collection = resources[resource_type]
    return _new_resource(collection, resource_type, len(collection) + 1, rng)


def _population_for(index: int, customer_count: int, rng: random.Random) -> str:
    populations = list(POPULATION_WEIGHTS)
    if index == customer_count - 1:
        return "coordinated_ring"
    choice = rng.random()
    cumulative = 0.0
    for population in populations:
        cumulative += POPULATION_WEIGHTS[population]
        if choice < cumulative:
            return population
    return populations[-1]


def _refund_probability(population: str, rng: random.Random) -> float:
    if population == "normal":
        return 0.08
    if population == "legit_high_refund":
        return rng.uniform(0.50, 0.78)
    if population == "coordinated_ring":
        if rng.random() < 0.40:
            return rng.uniform(0.55, 0.70)
        return rng.uniform(0.45, 0.65)
    return rng.uniform(0.70, 0.90)


def _refund_delay(population: str, rng: random.Random) -> timedelta:
    if population == "normal":
        return timedelta(days=rng.uniform(10, 45))
    if population == "legit_high_refund":
        if rng.random() < 0.18:
            return timedelta(hours=rng.uniform(2, 24))
        return timedelta(days=rng.uniform(1, 12))
    if rng.random() < 0.18:
        return timedelta(hours=rng.uniform(72, 168))
    return timedelta(hours=rng.uniform(2, 96))


def generate(
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    seed: int = DEFAULT_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return customers, orders, and refunds with reproducible event history.

    Every customer has independent device/IP/address fields. Coordinated-ring
    customers additionally share selected resource types within a group.
    """
    if customer_count < 8:
        raise ValueError("customer_count must be at least 8")

    rng = random.Random(seed)
    resources: dict[str, list[dict[str, Any]]] = {
        "device": [],
        "ip_address": [],
        "address": [],
    }
    customers: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    refunds: list[dict[str, Any]] = []
    ring_members: list[dict[str, Any]] = []
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

    for index in range(customer_count):
        population = _population_for(index, customer_count, rng)
        customer_id = f"cus_{index + 1:05d}"
        if population == "coordinated_ring":
            ring_members.append({"id": customer_id, "population": population})
            continue
        customer_resources = {
            resource_type: _resource_set(resources, resource_type, rng)
            for resource_type in resources
        }
        customers.append(
            {
                "id": customer_id,
                "external_id": customer_id,
                "population": population,
                "device_id": customer_resources["device"]["id"],
                "ip_address_id": customer_resources["ip_address"]["id"],
                "address_id": customer_resources["address"]["id"],
                "refund_ratio_target": round(_refund_probability(population, rng), 4),
                "events": [],
            }
        )

    # Allocate rings in contiguous groups so graph consumers can recover the
    # intended 4-8 member clusters from the shared resource IDs.
    ring_index = 0
    while ring_index < len(ring_members):
        remaining = len(ring_members) - ring_index
        valid_sizes = [
            size
            for size in range(4, min(8, remaining) + 1)
            if remaining - size == 0 or remaining - size >= 4
        ]
        group_size = rng.choice(valid_sizes)
        group = ring_members[ring_index : ring_index + group_size]
        ring_index += group_size
        shared = {
            "device": rng.random() < 0.70,
            "ip_address": rng.random() < 0.55,
            "address": rng.random() < 0.60,
        }
        if not any(shared.values()):
            shared[rng.choice(list(shared))] = True
        if all(shared.values()):
            shared[rng.choice(list(shared))] = False
        shared_resources = {
            resource_type: _resource_set(resources, resource_type, rng)
            for resource_type, is_shared in shared.items()
            if is_shared
        }
        for member in group:
            customer_resources = {}
            for resource_type in resources:
                customer_resources[resource_type] = (
                    shared_resources[resource_type]
                    if shared[resource_type]
                    else _resource_set(resources, resource_type, rng)
                )
            customers.append(
                {
                    "id": member["id"],
                    "external_id": member["id"],
                    "population": member["population"],
                    "ring_id": f"ring_{ring_index:04d}",
                    "device_id": customer_resources["device"]["id"],
                    "ip_address_id": customer_resources["ip_address"]["id"],
                    "address_id": customer_resources["address"]["id"],
                    "refund_ratio_target": round(_refund_probability(member["population"], rng), 4),
                    "events": [],
                }
            )

    customers.sort(key=lambda customer: customer["id"])
    for customer in customers:
        population = customer["population"]
        customer_start = base_time + timedelta(days=rng.uniform(0, 180))
        order_count = rng.randint(8, 14)
        refund_target = customer["refund_ratio_target"]
        refund_count = max(1 if refund_target > 0.4 else 0, round(order_count * refund_target))
        refund_orders = set(rng.sample(range(order_count), min(refund_count, order_count)))
        for order_number in range(order_count):
            order_id = f"ord_{len(orders) + 1:06d}"
            placed_at = customer_start + timedelta(days=order_number * rng.uniform(4, 12))
            delivered_at = placed_at + timedelta(days=rng.uniform(2, 7))
            order = {
                "id": order_id,
                "customer_id": customer["id"],
                "created_at": _timestamp(placed_at),
                "delivered_at": _timestamp(delivered_at),
                "device_id": customer["device_id"],
                "ip_address_id": customer["ip_address_id"],
                "address_id": customer["address_id"],
                "status": "completed",
            }
            orders.append(order)
            customer["events"].append(
                {"type": "order_placed", "timestamp": order["created_at"], "order_id": order_id}
            )
            customer["events"].append(
                {"type": "order_delivered", "timestamp": order["delivered_at"], "order_id": order_id}
            )
            if order_number in refund_orders:
                refund_at = delivered_at + _refund_delay(population, rng)
                refund_id = f"ref_{len(refunds) + 1:06d}"
                refund = {
                    "id": refund_id,
                    "order_id": order_id,
                    "customer_id": customer["id"],
                    "created_at": _timestamp(refund_at),
                    "device_id": customer["device_id"],
                    "ip_address_id": customer["ip_address_id"],
                    "address_id": customer["address_id"],
                    "status": "requested",
                }
                refunds.append(refund)
                customer["events"].append(
                    {"type": "refund_requested", "timestamp": refund["created_at"], "refund_id": refund_id}
                )
        customer["events"].sort(key=lambda event: event["timestamp"])

    return customers, orders, refunds


if __name__ == "__main__":
    generated_customers, generated_orders, generated_refunds = generate()
    print("customers:", len(generated_customers))
    print("orders:", len(generated_orders))
    print("refunds:", len(generated_refunds))
    print("populations:", Counter(customer["population"] for customer in generated_customers))
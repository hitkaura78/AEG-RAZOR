from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import get_current_user, require_role
from ..core.database import get_db
from ..core.models import AuditLog, Device, IPAddress, Order, Product, Refund, User
from ..core.pipeline import evaluate_refund_risk
from ..core.policy import decide, persisted_status

router = APIRouter(prefix="/api", tags=["customer"])

VELOCITY_WINDOW = timedelta(hours=2)
VELOCITY_THRESHOLD = 5


class OrderRequest(BaseModel):
    product_id: int
    device_fingerprint: str = Field(min_length=1, max_length=255)
    simulated_ip: str = Field(min_length=1, max_length=64)


class RefundRequest(BaseModel):
    order_id: int
    reason: str = Field(min_length=1, max_length=500)


def product_response(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "price": product.price,
        "category": product.category,
    }


def order_response(order: Order) -> dict:
    return {
        "id": order.id,
        "product": product_response(order.product),
        "amount": order.amount,
        "status": order.status,
        "created_at": order.created_at,
        "delivered_at": order.delivered_at,
    }


def refund_response(refund: Refund) -> dict:
    return {
        "id": refund.id,
        "order_id": refund.order_id,
        "amount": refund.amount,
        "reason": refund.reason,
        "status": refund.status,
        "created_at": refund.created_at,
    }


@router.get("/products")
def list_products(
    user: User = Depends(require_role("customer")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    return [product_response(product) for product in db.scalars(select(Product).order_by(Product.id))]


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    request: OrderRequest,
    user: User = Depends(require_role("customer")),
    db: Session = Depends(get_db),
) -> dict:
    if user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    product = db.get(Product, request.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    device = db.scalar(select(Device).where(Device.fingerprint == request.device_fingerprint))
    if device is None:
        device = Device(fingerprint=request.device_fingerprint)
        db.add(device)
        db.flush()

    ip_address = db.scalar(select(IPAddress).where(IPAddress.address == request.simulated_ip))
    if ip_address is None:
        ip_address = IPAddress(address=request.simulated_ip)
        db.add(ip_address)
        db.flush()

    now = datetime.now(timezone.utc)
    window_start = now - VELOCITY_WINDOW
    recent_orders = db.scalar(
        select(func.count(Order.id)).where(
            Order.created_at >= window_start,
            or_(Order.device_id == device.id, Order.ip_address_id == ip_address.id),
        )
    ) or 0

    policy_status, policy_reason = decide(
        final_score=0.60 if recent_orders + 1 >= VELOCITY_THRESHOLD else 0.0,
        reason_codes=["HIGH_REFUND_VELOCITY"] if recent_orders + 1 >= VELOCITY_THRESHOLD else [],
        cluster_size=1,
        shared_device=False,
        shared_ip=False,
        shared_address=False,
        cluster_refund_ratio=0.0,
    )

    order = Order(
        customer_id=user.customer_id,
        product_id=product.id,
        amount=product.price,
        status=persisted_status(policy_status),
        created_at=now,
        delivered_at=now + timedelta(days=3),
        device_id=device.id,
        ip_address_id=ip_address.id,
    )
    db.add(order)
    db.flush()

    db.add(AuditLog(
        event_name="order_created",
        actor_user_id=user.id,
        customer_id=user.customer_id,
        order_id=order.id,
        metadata_json=json.dumps({"product_id": product.id, "amount": product.price}),
    ))
    db.add(AuditLog(
        event_name="order_risk_evaluated",
        actor_user_id=user.id,
        customer_id=user.customer_id,
        order_id=order.id,
        metadata_json=json.dumps({
            "status": order.status,
            "policy_status": policy_status,
            "policy_reason": policy_reason,
            "recent_matching_orders": recent_orders,
        }),
    ))
    db.commit()
    db.refresh(order)
    return order_response(order)


@router.get("/orders")
def list_orders(
    user: User = Depends(require_role("customer")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    orders = db.scalars(
        select(Order).where(Order.customer_id == user.customer_id).order_by(Order.created_at.desc())
    )
    return [order_response(order) for order in orders]


@router.post("/refunds", status_code=status.HTTP_201_CREATED)
def request_refund(
    request: RefundRequest,
    user: User = Depends(require_role("customer")),
    db: Session = Depends(get_db),
) -> dict:
    if user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    order = db.get(Order, request.order_id)
    if order is None or order.customer_id != user.customer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    prior_order_count = db.scalar(
        select(func.count(Order.id)).where(
            Order.customer_id == user.customer_id,
            Order.id != order.id,
        )
    ) or 0
    prior_refund_count = db.scalar(
        select(func.count(Refund.id)).where(Refund.customer_id == user.customer_id)
    ) or 0
    history_state = "EXISTING" if prior_order_count + prior_refund_count > 0 else "NEW"
    created_at = datetime.now(timezone.utc)

    policy_status = evaluate_refund_risk(
        db=db,
        customer_id=user.customer_id,
        order_id=order.id,
        device_id=order.device_id,
        ip_address=order.ip_address.address,
    )

    refund = Refund(
        order_id=order.id,
        customer_id=user.customer_id,
        amount=order.amount,
        reason=request.reason.strip(),
        status=persisted_status(policy_status),
        created_at=created_at,
    )
    db.add(refund)
    db.flush()

    audit_metadata = {
        "customer_history": history_state,
        "prior_order_count": prior_order_count,
        "prior_refund_count": prior_refund_count,
        "device_id": order.device_id,
        "ip_address": order.ip_address.address,
    }
    db.add(AuditLog(
        event_name="refund_requested",
        actor_user_id=user.id,
        customer_id=user.customer_id,
        order_id=order.id,
        refund_id=refund.id,
        metadata_json=json.dumps(audit_metadata),
    ))
    db.commit()
    db.refresh(refund)
    return refund_response(refund)


@router.get("/refunds")
def list_refunds(
    user: User = Depends(require_role("customer")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    refunds = db.scalars(
        select(Refund).where(Refund.customer_id == user.customer_id).order_by(Refund.created_at.desc())
    )
    return [refund_response(refund) for refund in refunds]


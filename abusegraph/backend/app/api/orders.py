from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import get_current_user
from ..core.database import get_db
from ..core.models import AuditLog, Device, IPAddress, Order, Product, User

router = APIRouter(prefix="/api", tags=["orders"])
VELOCITY_WINDOW = timedelta(hours=2)
VELOCITY_THRESHOLD = 5


class OrderRequest(BaseModel):
    product_id: int
    device_fingerprint: str = Field(min_length=1, max_length=255)
    simulated_ip: str = Field(min_length=1, max_length=64)


def product_response(product: Product) -> dict:
    return {"id": product.id, "name": product.name, "price": product.price, "category": product.category}


def order_response(order: Order) -> dict:
    return {
        "id": order.id,
        "product": product_response(order.product),
        "amount": order.amount,
        "status": order.status,
        "created_at": order.created_at,
        "delivered_at": order.delivered_at,
    }


@router.get("/products")
def list_products(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if user.role != "customer" or user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    return [product_response(product) for product in db.scalars(select(Product).order_by(Product.id))]


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    request: OrderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "customer" or user.customer_id is None:
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
    order = Order(
        customer_id=user.customer_id,
        product_id=product.id,
        amount=product.price,
        status="PENDING_REVIEW" if recent_orders + 1 >= VELOCITY_THRESHOLD else "APPROVED",
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
        metadata_json=json.dumps({"status": order.status, "recent_matching_orders": recent_orders}),
    ))
    db.commit()
    db.refresh(order)
    return order_response(order)


@router.get("/orders")
def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    if user.role != "customer" or user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    orders = db.scalars(
        select(Order).where(Order.customer_id == user.customer_id).order_by(Order.created_at.desc())
    )
    return [order_response(order) for order in orders]
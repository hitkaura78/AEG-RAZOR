from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import get_current_user
from ..core.database import get_db
from ..core.models import AuditLog, Order, Refund, User
from ..core.refund_risk import evaluate_refund_risk

router = APIRouter(prefix="/api/refunds", tags=["refunds"])


class RefundRequest(BaseModel):
    order_id: int
    reason: str = Field(min_length=1, max_length=500)


def refund_response(refund: Refund) -> dict:
    return {
        "id": refund.id,
        "order_id": refund.order_id,
        "amount": refund.amount,
        "reason": refund.reason,
        "status": refund.status,
        "created_at": refund.created_at,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def request_refund(
    request: RefundRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "customer" or user.customer_id is None:
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
    risk_status = evaluate_refund_risk(
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
        status=risk_status,
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
    db.add(AuditLog(
        event_name="risk_evaluated",
        actor_user_id=user.id,
        customer_id=user.customer_id,
        order_id=order.id,
        refund_id=refund.id,
        metadata_json=json.dumps({**audit_metadata, "status": risk_status}),
    ))
    db.commit()
    db.refresh(refund)
    return refund_response(refund)


@router.get("")
def list_refunds(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    if user.role != "customer" or user.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    refunds = db.scalars(
        select(Refund).where(Refund.customer_id == user.customer_id).order_by(Refund.created_at.desc())
    )
    return [refund_response(refund) for refund in refunds]
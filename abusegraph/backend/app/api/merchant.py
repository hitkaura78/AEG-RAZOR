from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import require_role
from ..core.database import get_db
from ..core.models import AuditLog, Order, Refund, RiskCase, User
from ..core.policy import persisted_status

router = APIRouter(prefix="/api/merchant", tags=["merchant"])


class MerchantDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(accept|reject)$")
    note: str = ""


class MerchantCaseResponse(BaseModel):
    id: int
    case_type: str
    cluster_id: str | None = None
    num_customers: int
    num_orders: int
    num_refunds: int
    total_amount: float
    reason_codes: list[str]
    status: str
    agent_explanation: str | None = None
    agent_recommendation: str | None = None
    reviewer_decision: str | None = None
    reviewer_note: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


def parse_reason_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return list(data) if isinstance(data, list) else [str(data)]
    except Exception:
        return [raw]


@router.get("/review")
def review_queue(user: User = Depends(require_role("merchant"))) -> dict:
    return {"message": "Merchant review queue", "merchant_user_id": user.id}


@router.get("/orders")
def list_merchant_orders(
    user: User = Depends(require_role("merchant")),
    db: Session = Depends(get_db),
) -> list[dict]:
    orders = db.scalars(select(Order).order_by(Order.created_at.desc())).all()
    return [
        {
            "id": o.id,
            "customer_id": o.customer_id,
            "product_id": o.product_id,
            "amount": o.amount,
            "status": o.status,
            "created_at": o.created_at,
        }
        for o in orders
    ]


@router.get("/refunds")
def list_merchant_refunds(
    status_param: str | None = Query(default=None, alias="status"),
    user: User = Depends(require_role("merchant")),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Refund).order_by(Refund.created_at.desc(), Refund.id.desc())
    if status_param:
        if status_param in ("PENDING_REVIEW", "Review"):
            stmt = stmt.where(Refund.status.in_(["PENDING_REVIEW", "Review"]))
        else:
            stmt = stmt.where(Refund.status == status_param)
    refunds = db.scalars(stmt).all()

    cases = db.scalars(select(RiskCase).order_by(RiskCase.id.desc())).all()
    result = []
    for r in refunds:
        matched_case = next(
            (c for c in cases if c.id == r.id or str(r.customer_id) in (c.customer_ids or "")),
            cases[0] if cases else None,
        )
        result.append({
            "id": r.id,
            "order_id": r.order_id,
            "customer_id": r.customer_id,
            "amount": r.amount,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at,
            "case_id": matched_case.id if matched_case else r.id,
        })
    return result


@router.get("/cases/{case_id}", response_model=MerchantCaseResponse)
def get_merchant_case(
    case_id: int,
    user: User = Depends(require_role("merchant")),
    db: Session = Depends(get_db),
) -> MerchantCaseResponse:
    case = db.get(RiskCase, case_id)
    if case is None:
        case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk case not found")
    return MerchantCaseResponse(
        id=case.id,
        case_type=case.case_type,
        cluster_id=case.cluster_id,
        num_customers=case.num_customers,
        num_orders=case.num_orders,
        num_refunds=case.num_refunds,
        total_amount=case.total_amount,
        reason_codes=parse_reason_codes(case.reason_codes),
        status=case.status,
        agent_explanation=case.agent_explanation,
        agent_recommendation=case.agent_recommendation,
        reviewer_decision=case.reviewer_decision,
        reviewer_note=case.reviewer_note,
        created_at=case.created_at,
    )


@router.post("/cases/{case_id}/decision", response_model=MerchantCaseResponse)
def make_merchant_decision(
    case_id: int,
    request: MerchantDecisionRequest,
    user: User = Depends(require_role("merchant")),
    db: Session = Depends(get_db),
) -> MerchantCaseResponse:
    case = db.get(RiskCase, case_id)
    if case is None:
        case = db.scalar(select(RiskCase).order_by(RiskCase.id.desc()))
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk case not found")

    new_policy_status = "Allow" if request.decision == "accept" else "Restrict"
    new_public_status = persisted_status(new_policy_status)

    case.reviewer_decision = request.decision
    case.reviewer_note = request.note.strip() if request.note else None
    case.status = new_policy_status

    # Synchronize matching Refund and Order statuses
    db_refunds = db.scalars(select(Refund).where(Refund.status.in_(["PENDING_REVIEW", "Review"]))).all()
    for r in db_refunds:
        r.status = new_public_status
        order = db.get(Order, r.order_id)
        if order:
            order.status = new_public_status

    db.add(AuditLog(
        event_name="merchant_decision",
        actor_user_id=user.id,
        case_id=case.id,
        actor="merchant",
        action=f"decision_{request.decision}",
        details=json.dumps({"decision": request.decision, "note": request.note}),
        metadata_json=json.dumps({"new_status": new_public_status}),
    ))
    db.commit()
    db.refresh(case)

    return MerchantCaseResponse(
        id=case.id,
        case_type=case.case_type,
        cluster_id=case.cluster_id,
        num_customers=case.num_customers,
        num_orders=case.num_orders,
        num_refunds=case.num_refunds,
        total_amount=case.total_amount,
        reason_codes=parse_reason_codes(case.reason_codes),
        status=case.status,
        agent_explanation=case.agent_explanation,
        agent_recommendation=case.agent_recommendation,
        reviewer_decision=case.reviewer_decision,
        reviewer_note=case.reviewer_note,
        created_at=case.created_at,
    )
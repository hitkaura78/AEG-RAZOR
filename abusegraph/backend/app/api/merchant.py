from fastapi import APIRouter, Depends

from ..core.auth import require_role
from ..core.models import User

router = APIRouter(prefix="/api/merchant", tags=["merchant"])


@router.get("/review")
def review_queue(user: User = Depends(require_role("merchant"))) -> dict:
    return {"message": "Merchant review queue", "merchant_user_id": user.id}
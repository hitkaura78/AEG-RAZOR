from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import create_access_token, get_current_user, hash_password, verify_password
from ..core.database import get_db
from ..core.models import Customer, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


def user_profile(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "customer_id": user.customer_id,
        "profile": {"name": user.customer.name} if user.customer else None,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    email = request.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    customer = Customer(name=request.name.strip())
    db.add(customer)
    db.flush()
    user = User(email=email, password_hash=hash_password(request.password), role="customer", customer_id=customer.id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_profile(user)


@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == request.email.strip().lower()))
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return {"access_token": create_access_token(user), "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return user_profile(user)
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    user: Mapped["User | None"] = relationship(back_populates="customer", uselist=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    customer: Mapped[Customer | None] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    line1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="")


class CustomerDeviceUsage(Base):
    __tablename__ = "customer_device_usage"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerIPAddressUsage(Base):
    __tablename__ = "customer_ip_address_usage"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    ip_address_id: Mapped[int] = mapped_column(ForeignKey("ip_addresses.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerAddressUsage(Base):
    __tablename__ = "customer_address_usage"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    address_id: Mapped[int] = mapped_column(ForeignKey("addresses.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IPAddress(Base):
    __tablename__ = "ip_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    ip_address_id: Mapped[int] = mapped_column(ForeignKey("ip_addresses.id"), nullable=False, index=True)

    product: Mapped[Product] = relationship()
    device: Mapped[Device] = relationship()
    ip_address: Mapped[IPAddress] = relationship()


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    order: Mapped[Order] = relationship()


class RiskCase(Base):
    __tablename__ = "risk_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    cluster_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    cluster_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    customer_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    num_customers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_refunds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ml_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    graph_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewer_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    refund_id: Mapped[int | None] = mapped_column(ForeignKey("refunds.id"), nullable=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("risk_cases.id"), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    actor: Mapped[str] = mapped_column(String(30), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(100), nullable=False, default="legacy_event")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    metadata_json: Mapped[str] = mapped_column(String(2000), default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
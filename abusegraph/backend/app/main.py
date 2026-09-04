from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select, text

from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.customer import router as customer_router
from .api.merchant import router as merchant_router
from .core.auth import hash_password
from .core.database import Base, SessionLocal, engine
from .core.models import Product, User

DEMO_MERCHANT_EMAIL = "merchant@demo.abusegraph"
DEMO_MERCHANT_PASSWORD = "DemoMerchant123!"
DEMO_ADMIN_EMAIL = "admin@demo.abusegraph"
DEMO_ADMIN_PASSWORD = "DemoAdmin123!"
DEMO_PRODUCTS = (
    ("Everyday Backpack", 49.99, "Accessories"),
    ("Wireless Headphones", 89.00, "Electronics"),
    ("Ceramic Travel Mug", 24.50, "Home"),
    ("Running Shoes", 119.00, "Apparel"),
    ("Mechanical Keyboard", 109.99, "Electronics"),
    ("Desk Lamp", 39.95, "Home"),
    ("Cotton Hoodie", 64.00, "Apparel"),
    ("Stainless Water Bottle", 29.99, "Accessories"),
    ("Notebook Set", 18.00, "Stationery"),
    ("Portable Charger", 44.90, "Electronics"),
)


def seed_demo_users() -> None:
    with SessionLocal() as db:
        for email, password, role in (
            (DEMO_MERCHANT_EMAIL, DEMO_MERCHANT_PASSWORD, "merchant"),
            (DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, "admin"),
        ):
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(User(email=email, password_hash=hash_password(password), role=role))
        db.commit()


def seed_demo_products() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Product.id)) is None:
            db.add_all(
                Product(name=name, price=price, category=category)
                for name, price, category in DEMO_PRODUCTS
            )
            db.commit()


def migrate_schema() -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "audit_logs" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("audit_logs")}
        migrations = {
            "refund_id": "ALTER TABLE audit_logs ADD COLUMN refund_id INTEGER",
            "case_id": "ALTER TABLE audit_logs ADD COLUMN case_id INTEGER",
            "timestamp": "ALTER TABLE audit_logs ADD COLUMN timestamp DATETIME",
            "actor": "ALTER TABLE audit_logs ADD COLUMN actor VARCHAR(30) NOT NULL DEFAULT 'system'",
            "action": "ALTER TABLE audit_logs ADD COLUMN action VARCHAR(100) NOT NULL DEFAULT 'legacy_event'",
            "details": "ALTER TABLE audit_logs ADD COLUMN details TEXT NOT NULL DEFAULT '{}'",
        }
        with engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    seed_demo_users()
    seed_demo_products()
    yield


app = FastAPI(title="AbuseGraph", lifespan=lifespan)

# Enable CORS for local frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount role-scoped API routers
app.include_router(auth_router)
app.include_router(customer_router)
app.include_router(merchant_router)
app.include_router(admin_router)

# Mount static frontend directory
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
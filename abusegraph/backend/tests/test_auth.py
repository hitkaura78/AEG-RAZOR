from fastapi.testclient import TestClient
from uuid import uuid4

from backend.app.main import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_MERCHANT_EMAIL,
    DEMO_MERCHANT_PASSWORD,
    app,
)


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_customer_register_login_and_role_protection() -> None:
    with TestClient(app) as client:
        email = f"customer-{uuid4().hex}@example.com"
        response = client.post(
            "/api/auth/register",
            json={"email": email, "password": "CustomerPass123!", "name": "New Customer"},
        )
        assert response.status_code == 201
        token = login(client, email, "CustomerPass123!")
        me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_response.status_code == 200
        assert me_response.json()["role"] == "customer"
        forbidden = client.get("/api/merchant/review", headers={"Authorization": f"Bearer {token}"})
        assert forbidden.status_code == 403


def test_seeded_merchant_and_admin_roles() -> None:
    with TestClient(app) as client:
        merchant_token = login(client, DEMO_MERCHANT_EMAIL, DEMO_MERCHANT_PASSWORD)
        merchant_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {merchant_token}"})
        assert merchant_me.json()["role"] == "merchant"

        admin_token = login(client, DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD)
        admin_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_me.json()["role"] == "admin"


def test_auth_invalid_credentials_handling() -> None:
    with TestClient(app) as client:
        # Invalid password
        bad_pass = client.post("/api/auth/login", json={"email": DEMO_ADMIN_EMAIL, "password": "WrongPassword123!"})
        assert bad_pass.status_code == 401

        # Non-existent user
        no_user = client.post("/api/auth/login", json={"email": "nonexistent@example.com", "password": "Password123!"})
        assert no_user.status_code == 401

        # Missing token header
        no_token = client.get("/api/auth/me")
        assert no_token.status_code == 401
from fastapi.testclient import TestClient
from uuid import uuid4
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.api.v1.auth import _create_password_reset_token
from app.models.user import User


def _login(client: TestClient, email: str, password: str = "StrongPass1"):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def test_register_success(client: TestClient):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "register@example.com",
            "full_name": "Register User",
            "phone": "9876543210",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["email"] == "register@example.com"


def test_login_success(client: TestClient):
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "full_name": "Login User",
            "phone": "9876543211",
            "password": "StrongPass1",
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "login@example.com",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["user"]["email"] == "login@example.com"
    assert "access_token" not in payload["data"]
    assert "refresh_token" not in payload["data"]
    assert response.cookies.get("access_token") is not None
    assert response.cookies.get("refresh_token") is not None

    profile_response = client.get("/api/v1/users/me")
    assert profile_response.status_code == 200
    profile = profile_response.json()["data"]
    assert profile["email"] == "login@example.com"
    assert "password_hash" not in profile
    assert "session_version" not in profile


def test_login_failure(client: TestClient):
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "wrongpass@example.com",
            "full_name": "Wrong Password",
            "phone": "9876543212",
            "password": "StrongPass1",
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "wrongpass@example.com",
            "password": "WrongPass1",
        },
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False


def test_logout_revokes_access_token(client: TestClient):
    email = f"revoke-{uuid4()}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "full_name": "Revoke User",
            "phone": "9876543291",
            "password": "StrongPass1",
        },
    )
    assert register_response.status_code == 201

    login_response = _login(client, email)
    assert login_response.status_code == 200
    old_access_token = login_response.cookies.get("access_token")
    assert old_access_token is not None

    csrf_response = client.get("/api/v1/auth/csrf-token")
    csrf_token = csrf_response.cookies.get("csrf_token")
    logout_response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_response.status_code == 200

    client.cookies.set("access_token", old_access_token)
    revoked_response = client.get("/api/v1/cart/")
    assert revoked_response.status_code == 401
    assert revoked_response.json()["message"] == "Token has been revoked"


def test_login_rotates_session_version_and_invalidates_old_token(client: TestClient):
    email = f"session-{uuid4()}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "full_name": "Session User",
            "phone": "9876543292",
            "password": "StrongPass1",
        },
    )
    assert register_response.status_code == 201

    first_login = _login(client, email)
    assert first_login.status_code == 200
    first_access_token = first_login.cookies.get("access_token")
    assert first_access_token is not None
    first_payload = decode_token(first_access_token)

    second_login = _login(client, email)
    assert second_login.status_code == 200
    second_access_token = second_login.cookies.get("access_token")
    assert second_access_token is not None
    second_payload = decode_token(second_access_token)

    assert int(second_payload["session_version"]) > int(first_payload["session_version"])

    client.cookies.set("access_token", first_access_token)
    old_token_response = client.get("/api/v1/cart/")
    assert old_token_response.status_code == 401
    assert old_token_response.json()["message"] == "Session has been invalidated. Please login again."


def test_password_reset_token_is_single_use(client: TestClient, db_session: Session):
    email = f"reset-{uuid4()}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "full_name": "Reset User",
            "phone": "9876543293",
            "password": "StrongPass1",
        },
    )
    assert register_response.status_code == 201
    user = db_session.query(User).filter(User.email == email).one()
    token = _create_password_reset_token(user)

    reset_response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "NewStrongPass2"},
    )
    assert reset_response.status_code == 200
    assert _login(client, email, "NewStrongPass2").status_code == 200

    replay_response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "AnotherStrong3"},
    )
    assert replay_response.status_code == 400

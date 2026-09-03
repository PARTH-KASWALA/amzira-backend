from __future__ import annotations

import httpx

from scripts.verify_production_launch import run


SITE = "https://www.amzira.com"
API = "https://api.amzira.com"
NO_STORE = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _public_response(request: httpx.Request, *, checkout: bool = False) -> httpx.Response:
    path = request.url.path
    if path == "/health":
        return httpx.Response(200, json={"status": "healthy", "environment": "production"})
    if path == "/api/v1/commerce/status":
        return httpx.Response(
            200, json={"checkout_enabled": checkout, "cod_enabled": False}
        )
    if path == "/api/v1/admin/orders" and "access_token" not in request.headers.get("cookie", ""):
        return httpx.Response(401, headers=NO_STORE, json={"detail": "unauthorized"})
    if path == "/seller/login":
        return httpx.Response(
            200,
            headers={
                **NO_STORE,
                "X-Robots-Tag": "noindex, nofollow, noarchive",
            },
            text="seller",
        )
    if path == "/robots.txt":
        return httpx.Response(200, text="User-Agent: *\nDisallow: /seller\n")
    if path == "/api/v1/products" and request.method == "OPTIONS":
        origin = request.headers.get("origin")
        headers = {}
        status = 400
        if origin == SITE:
            status = 200
            headers = {
                "Access-Control-Allow-Origin": SITE,
                "Access-Control-Allow-Credentials": "true",
            }
        return httpx.Response(status, headers=headers)
    if path == "/api/v1/products":
        return httpx.Response(
            200,
            json={
                "data": {
                    "total": 1,
                    "products": [
                        {
                            "name": "must-never-be-printed",
                            "primary_image": "https://cdn.amzira.com/catalog/image.webp",
                        }
                    ],
                }
            },
        )
    if request.url.host == "cdn.amzira.com" and path == "/catalog/image.webp":
        return httpx.Response(200, headers={"Content-Type": "image/webp"})
    raise AssertionError(f"Unexpected request: {request.method} {request.url}")


def test_public_acceptance_passes_without_secrets(capsys):
    exit_code = run([], transport=httpx.MockTransport(_public_response))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Summary: 9 passed, 0 failed, 2 skipped" in output
    assert "must-never-be-printed" not in output
    assert "[PASS] Commerce launch lock: checkout disabled; COD disabled" in output


def test_unexpected_checkout_enablement_fails_closed(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return _public_response(request, checkout=True)

    exit_code = run([], transport=httpx.MockTransport(handler))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Commerce launch lock" in output
    assert "1 failed" in output


def test_required_optional_groups_fail_when_secrets_are_missing(capsys, monkeypatch):
    for name in (
        "AMZIRA_HEALTHCHECK_TOKEN",
        "AMZIRA_SELLER_EMAIL",
        "AMZIRA_SELLER_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = run(
        ["--require-protected-health", "--require-seller"],
        transport=httpx.MockTransport(_public_response),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] Protected service health" in output
    assert "[FAIL] Seller acceptance" in output


def test_protected_health_and_seller_acceptance_are_privacy_safe(
    capsys, monkeypatch
):
    monkeypatch.setenv("AMZIRA_HEALTHCHECK_TOKEN", "health-secret")
    monkeypatch.setenv("AMZIRA_SELLER_EMAIL", "private-admin@example.com")
    monkeypatch.setenv("AMZIRA_SELLER_PASSWORD", "private-password")

    protected_health = {
        "/health/database",
        "/health/email",
        "/health/email/queue",
        "/health/catalog-launch",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in protected_health:
            assert request.headers["x-health-token"] == "health-secret"
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/api/v1/auth/csrf-token":
            return httpx.Response(
                200,
                headers={"Set-Cookie": "csrf_token=csrf-secret; Path=/; Domain=.amzira.com"},
                json={"success": True},
            )
        if path == "/api/v1/auth/login":
            assert request.headers["x-csrf-token"] == "csrf-secret"
            assert request.headers["origin"] == SITE
            assert request.content == b'{"email":"private-admin@example.com","password":"private-password"}'
            return httpx.Response(
                200,
                headers={"Set-Cookie": "access_token=access-secret; Path=/; Domain=.amzira.com"},
                json={"success": True},
            )
        if path == "/api/v1/users/me":
            return httpx.Response(
                200,
                headers=NO_STORE,
                json={"data": {"role": "admin", "email": "private-admin@example.com"}},
            )
        if path == "/api/v1/admin/orders" and request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                headers=NO_STORE,
                json={
                    "data": {
                        "total": 1,
                        "orders": [
                            {
                                "id": 41,
                                "order_number": "AMZ-PRIVATE-ORDER",
                                "customer_email": "customer@example.com",
                            }
                        ],
                    }
                },
            )
        if path == "/api/v1/admin/orders/41":
            return httpx.Response(
                200,
                headers=NO_STORE,
                json={
                    "data": {
                        "customer": {"email": "customer@example.com"},
                        "shipping_address": {"address_line1": "private address"},
                        "items": [],
                        "payment": {"status": "paid"},
                        "status": "placed",
                    }
                },
            )
        if path == "/api/v1/auth/logout":
            assert request.headers["x-csrf-token"] == "csrf-secret"
            return httpx.Response(200, json={"success": True})
        return _public_response(request)

    exit_code = run(
        ["--require-protected-health", "--require-seller"],
        transport=httpx.MockTransport(handler),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Summary: 18 passed, 0 failed, 0 skipped" in output
    for secret in (
        "health-secret",
        "csrf-secret",
        "access-secret",
        "private-password",
        "private-admin@example.com",
        "AMZ-PRIVATE-ORDER",
        "customer@example.com",
        "private address",
    ):
        assert secret not in output

#!/usr/bin/env python3
"""Privacy-safe acceptance checks for an AMZIRA production release.

The default invocation verifies public launch controls and requires checkout and
COD to remain disabled. Protected health and seller checks run only when their
credentials are supplied through environment variables. Seller acceptance creates
and closes a login session but never mutates commerce data. No response body,
credential, token, order number, or customer field is printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urljoin, urlsplit

import httpx


DEFAULT_SITE_URL = "https://www.amzira.com"
DEFAULT_API_URL = "https://api.amzira.com/api/v1"
UNTRUSTED_ORIGIN = "https://not-amzira.invalid"
NO_STORE_DIRECTIVES = {"private", "no-store", "max-age=0"}


class VerificationError(RuntimeError):
    """A safe-to-display acceptance failure."""


@dataclass(frozen=True)
class CheckResult:
    state: Literal["PASS", "FAIL", "SKIP"]
    name: str
    detail: str


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid HTTP(S) URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _require_status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise VerificationError(
            f"expected HTTP {expected}; received HTTP {response.status_code}"
        )


def _json_object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise VerificationError("response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise VerificationError("JSON response was not an object")
    return payload


def _require_no_store(
    response: httpx.Response, *, require_legacy_headers: bool = True
) -> None:
    cache_control = {
        directive.strip().lower()
        for directive in response.headers.get("cache-control", "").split(",")
        if directive.strip()
    }
    missing = sorted(NO_STORE_DIRECTIVES - cache_control)
    if missing:
        raise VerificationError(
            "missing cache-control directive(s): " + ", ".join(missing)
        )
    if require_legacy_headers:
        if response.headers.get("pragma", "").strip().lower() != "no-cache":
            raise VerificationError("missing Pragma: no-cache")
        if response.headers.get("expires", "").strip() != "0":
            raise VerificationError("missing Expires: 0")


def _cookie_value(client: httpx.Client, name: str) -> str | None:
    for cookie in client.cookies.jar:
        if cookie.name == name:
            return cookie.value
    return None


class ProductionVerifier:
    def __init__(
        self,
        *,
        site_url: str,
        api_url: str,
        expect_checkout_enabled: bool,
        expect_cod_enabled: bool,
        client: httpx.Client,
    ) -> None:
        self.site_url = site_url.rstrip("/")
        self.site_origin = _origin(self.site_url)
        self.api_url = api_url.rstrip("/")
        self.api_origin = _origin(self.api_url)
        self.expect_checkout_enabled = expect_checkout_enabled
        self.expect_cod_enabled = expect_cod_enabled
        self.client = client
        self.results: list[CheckResult] = []
        self._catalog_image_url: str | None = None
        self._first_order_id: int | None = None

    def check(self, name: str, operation: Callable[[], str]) -> bool:
        try:
            detail = operation()
        except VerificationError as exc:
            self.results.append(CheckResult("FAIL", name, str(exc)))
            return False
        except httpx.HTTPError as exc:
            self.results.append(
                CheckResult("FAIL", name, f"network error ({type(exc).__name__})")
            )
            return False
        except Exception as exc:  # Defensive: output only the class, never raw data.
            self.results.append(
                CheckResult("FAIL", name, f"unexpected error ({type(exc).__name__})")
            )
            return False
        self.results.append(CheckResult("PASS", name, detail))
        return True

    def skip(self, name: str, detail: str) -> None:
        self.results.append(CheckResult("SKIP", name, detail))

    def _get(self, url: str, **kwargs) -> httpx.Response:
        return self.client.get(url, **kwargs)

    def run_public(self) -> None:
        self.check("API liveness", self._check_liveness)
        self.check("Commerce launch lock", self._check_commerce_lock)
        self.check("Admin authorization and privacy", self._check_unauthorized_admin)
        self.check("Seller page privacy", self._check_seller_page)
        self.check("Seller robots exclusion", self._check_robots)
        self.check("Production-origin CORS", self._check_allowed_cors)
        self.check("Untrusted-origin CORS", self._check_untrusted_cors)
        if self.check("Public catalog", self._check_catalog):
            self.check("Catalog image delivery", self._check_catalog_image)
        else:
            self.skip("Catalog image delivery", "catalog prerequisite failed")

    def _check_liveness(self) -> str:
        response = self._get(f"{self.api_origin}/health")
        _require_status(response, 200)
        payload = _json_object(response)
        if payload.get("status") != "healthy":
            raise VerificationError("API did not report healthy")
        if payload.get("environment") != "production":
            raise VerificationError("API did not report the production environment")
        return "healthy production API"

    def _check_commerce_lock(self) -> str:
        response = self._get(f"{self.api_url}/commerce/status")
        _require_status(response, 200)
        payload = _json_object(response)
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise VerificationError("commerce status data was not an object")
        actual_checkout = data.get("checkout_enabled")
        actual_cod = data.get("cod_enabled")
        if actual_checkout is not self.expect_checkout_enabled:
            raise VerificationError(
                "checkout state differed from the explicit launch expectation"
            )
        if actual_cod is not self.expect_cod_enabled:
            raise VerificationError("COD state differed from the explicit launch expectation")
        state = "enabled" if actual_checkout else "disabled"
        cod_state = "enabled" if actual_cod else "disabled"
        return f"checkout {state}; COD {cod_state}"

    def _check_unauthorized_admin(self) -> str:
        response = self._get(f"{self.api_url}/admin/orders", params={"limit": 1})
        _require_status(response, 401)
        _require_no_store(response)
        return "unauthenticated access denied with no-store headers"

    def _check_seller_page(self) -> str:
        response = self._get(f"{self.site_url}/seller/login")
        _require_status(response, 200)
        _require_no_store(response, require_legacy_headers=False)
        robots = response.headers.get("x-robots-tag", "").lower()
        if "noindex" not in robots or "nofollow" not in robots:
            raise VerificationError("seller page lacked noindex/nofollow headers")
        return "seller login is private and non-indexable"

    def _check_robots(self) -> str:
        response = self._get(f"{self.site_url}/robots.txt")
        _require_status(response, 200)
        directives = {line.strip().lower() for line in response.text.splitlines()}
        if "disallow: /seller" not in directives:
            raise VerificationError("robots.txt did not disallow /seller")
        return "robots.txt excludes /seller"

    def _preflight(self, origin: str) -> httpx.Response:
        return self.client.options(
            f"{self.api_url}/products",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    def _check_allowed_cors(self) -> str:
        response = self._preflight(self.site_origin)
        _require_status(response, 200)
        allowed = response.headers.get("access-control-allow-origin")
        if allowed != self.site_origin:
            raise VerificationError("production storefront origin was not explicitly allowed")
        if response.headers.get("access-control-allow-credentials", "").lower() != "true":
            raise VerificationError("credentialed CORS was not enabled for the storefront")
        return "storefront origin explicitly allowed"

    def _check_untrusted_cors(self) -> str:
        response = self._preflight(UNTRUSTED_ORIGIN)
        allowed = response.headers.get("access-control-allow-origin")
        if allowed in {"*", UNTRUSTED_ORIGIN}:
            raise VerificationError("untrusted origin received an allow-origin header")
        return "untrusted origin rejected"

    def _check_catalog(self) -> str:
        response = self._get(f"{self.api_url}/products", params={"limit": 1})
        _require_status(response, 200)
        payload = _json_object(response)
        data = payload.get("data")
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list) or not products:
            raise VerificationError("catalog contained no launch product")
        first = products[0]
        if not isinstance(first, dict):
            raise VerificationError("catalog product shape was invalid")
        image_url = first.get("primary_image")
        if not isinstance(image_url, str) or not image_url.strip():
            raise VerificationError("launch product had no primary image")
        self._catalog_image_url = urljoin(f"{self.api_origin}/", image_url)
        total = data.get("total")
        if not isinstance(total, int) or total < 1:
            raise VerificationError("catalog total was invalid")
        return "active catalog product available"

    def _check_catalog_image(self) -> str:
        if not self._catalog_image_url:
            raise VerificationError("catalog prerequisite did not provide an image")
        response = self.client.head(self._catalog_image_url)
        _require_status(response, 200)
        if not response.headers.get("content-type", "").lower().startswith("image/"):
            raise VerificationError("catalog asset did not return an image content type")
        return "primary product image available"

    def run_protected_health(self, token: str | None, *, required: bool) -> None:
        if not token:
            state = "FAIL" if required else "SKIP"
            detail = "health token environment variable is missing"
            self.results.append(CheckResult(state, "Protected service health", detail))
            return

        headers = {"X-Health-Token": token}
        endpoints = (
            ("Database health", "/health/database"),
            ("Email worker health", "/health/email"),
            ("Email queue health", "/health/email/queue"),
            ("Launch catalog health", "/health/catalog-launch"),
        )
        for name, path in endpoints:
            self.check(
                name,
                lambda path=path, headers=headers: self._check_protected_health(
                    path, headers
                ),
            )

    def _check_protected_health(self, path: str, headers: dict[str, str]) -> str:
        response = self._get(f"{self.api_origin}{path}", headers=headers)
        _require_status(response, 200)
        payload = _json_object(response)
        if payload.get("status") != "healthy":
            raise VerificationError("service did not report healthy")
        return "service reports healthy"

    def run_seller(
        self,
        email: str | None,
        password: str | None,
        *,
        required: bool,
    ) -> None:
        if not email and not password:
            state = "FAIL" if required else "SKIP"
            self.results.append(
                CheckResult(state, "Seller acceptance", "seller credential variables are missing")
            )
            return
        if not email or not password:
            self.results.append(
                CheckResult(
                    "FAIL",
                    "Seller acceptance",
                    "both seller email and password variables are required",
                )
            )
            return

        logged_in = False
        try:
            logged_in = self.check(
                "Seller authentication", lambda: self._check_seller_login(email, password)
            )
            if not logged_in:
                self.skip("Seller authorization", "authentication prerequisite failed")
                self.skip("Seller order list", "authentication prerequisite failed")
                self.skip("Seller order detail", "authentication prerequisite failed")
                return
            if not self.check("Seller authorization", self._check_seller_role):
                self.skip("Seller order list", "admin-role prerequisite failed")
                self.skip("Seller order detail", "admin-role prerequisite failed")
                return
            if self.check("Seller order list", self._check_seller_orders):
                if self._first_order_id is not None:
                    self.check("Seller order detail", self._check_seller_order_detail)
                else:
                    self.skip("Seller order detail", "no production order exists yet")
            else:
                self.skip("Seller order detail", "order-list prerequisite failed")
        finally:
            if logged_in:
                self.check("Seller session cleanup", self._logout)

    def _csrf_headers(self) -> dict[str, str]:
        token = _cookie_value(self.client, "csrf_token")
        if not token:
            raise VerificationError("CSRF cookie was not issued")
        return {"X-CSRF-Token": token, "Origin": self.site_origin}

    def _check_seller_login(self, email: str, password: str) -> str:
        csrf = self._get(f"{self.api_url}/auth/csrf-token")
        _require_status(csrf, 200)
        headers = self._csrf_headers()
        response = self.client.post(
            f"{self.api_url}/auth/login",
            json={"email": email, "password": password},
            headers=headers,
        )
        _require_status(response, 200)
        if not _cookie_value(self.client, "access_token"):
            raise VerificationError("authenticated session cookie was not issued")
        return "admin session established"

    def _check_seller_role(self) -> str:
        response = self._get(f"{self.api_url}/users/me")
        _require_status(response, 200)
        _require_no_store(response)
        payload = _json_object(response)
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("role") != "admin":
            raise VerificationError("authenticated account did not have the admin role")
        return "admin role verified server-side"

    def _check_seller_orders(self) -> str:
        response = self._get(f"{self.api_url}/admin/orders", params={"page": 1, "limit": 1})
        _require_status(response, 200)
        _require_no_store(response)
        payload = _json_object(response)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise VerificationError("order-list data was not an object")
        orders = data.get("orders")
        total = data.get("total")
        if not isinstance(orders, list) or not isinstance(total, int) or total < 0:
            raise VerificationError("order-list pagination shape was invalid")
        self._first_order_id = None
        if orders:
            first = orders[0]
            if not isinstance(first, dict) or not isinstance(first.get("id"), int):
                raise VerificationError("order-list item shape was invalid")
            self._first_order_id = first["id"]
        return f"protected order list available ({total} total)"

    def _check_seller_order_detail(self) -> str:
        order_id = getattr(self, "_first_order_id", None)
        if not isinstance(order_id, int):
            raise VerificationError("order-list prerequisite did not provide an ID")
        response = self._get(f"{self.api_url}/admin/orders/{order_id}")
        _require_status(response, 200)
        _require_no_store(response)
        payload = _json_object(response)
        data = payload.get("data")
        required = {"customer", "shipping_address", "items", "payment", "status"}
        if not isinstance(data, dict) or not required.issubset(data):
            raise VerificationError("order-detail schema was incomplete")
        if not isinstance(data.get("items"), list):
            raise VerificationError("order-detail items shape was invalid")
        return "protected order detail schema available"

    def _logout(self) -> str:
        response = self.client.post(
            f"{self.api_url}/auth/logout", headers=self._csrf_headers()
        )
        _require_status(response, 200)
        return "admin session closed"


def _enabled(value: str) -> bool:
    return value == "enabled"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run privacy-safe AMZIRA production acceptance checks. Defaults require "
            "checkout and COD to remain disabled."
        )
    )
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument(
        "--expect-checkout",
        choices=("disabled", "enabled"),
        default="disabled",
    )
    parser.add_argument(
        "--expect-cod", choices=("disabled", "enabled"), default="disabled"
    )
    parser.add_argument(
        "--health-token-env", default="AMZIRA_HEALTHCHECK_TOKEN"
    )
    parser.add_argument(
        "--seller-email-env", default="AMZIRA_SELLER_EMAIL"
    )
    parser.add_argument(
        "--seller-password-env", default="AMZIRA_SELLER_PASSWORD"
    )
    parser.add_argument("--require-protected-health", action="store_true")
    parser.add_argument("--require-seller", action="store_true")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    args = parse_args(argv)
    if args.timeout_seconds <= 0:
        print("[FAIL] Configuration: timeout must be greater than zero")
        return 2

    try:
        timeout = httpx.Timeout(args.timeout_seconds)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": "amzira-production-verifier/1.0"},
        ) as client:
            verifier = ProductionVerifier(
                site_url=args.site_url,
                api_url=args.api_url,
                expect_checkout_enabled=_enabled(args.expect_checkout),
                expect_cod_enabled=_enabled(args.expect_cod),
                client=client,
            )
            verifier.run_public()
            verifier.run_protected_health(
                os.getenv(args.health_token_env),
                required=args.require_protected_health,
            )
            verifier.run_seller(
                os.getenv(args.seller_email_env),
                os.getenv(args.seller_password_env),
                required=args.require_seller,
            )
    except ValueError as exc:
        print(f"[FAIL] Configuration: {exc}")
        return 2

    for result in verifier.results:
        print(f"[{result.state}] {result.name}: {result.detail}")

    passed = sum(result.state == "PASS" for result in verifier.results)
    failed = sum(result.state == "FAIL" for result in verifier.results)
    skipped = sum(result.state == "SKIP" for result in verifier.results)
    print(f"Summary: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

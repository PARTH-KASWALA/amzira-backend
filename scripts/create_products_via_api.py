#!/usr/bin/env python3
"""Create one product per category (men/women/kids) via backend admin APIs.

Flow:
1) Fetch CSRF token
2) Login as admin
3) Resolve category IDs from public categories API
4) Create 3 products with image upload
5) Add one variant to each product
6) Ensure is_active=true
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

_FORMAT_TO_EXT_MIME = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "JPG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


class ApiError(RuntimeError):
    pass


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _require_success(response: httpx.Response, context: str) -> Dict[str, Any]:
    payload = _json_or_text(response)
    if response.status_code >= 400:
        raise ApiError(f"{context} failed ({response.status_code}): {payload}")
    if not isinstance(payload, dict):
        raise ApiError(f"{context} returned non-JSON payload: {payload}")
    if payload.get("success") is False:
        raise ApiError(f"{context} returned success=false: {payload}")
    return payload


def _csrf_headers(client: httpx.Client) -> Dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        raise ApiError("Missing csrf_token cookie; call /api/v1/auth/csrf-token first")
    return {CSRF_HEADER_NAME: token}


def _detect_filename_and_content_type(image_path: Path) -> tuple[str, str]:
    """
    Some of the seed images may have a .jpg filename but actually be PNG.
    The backend validates MIME type and extension, so we send a filename/content-type
    that matches the actual image format.
    """
    try:
        from PIL import Image  # type: ignore

        with Image.open(image_path) as im:
            fmt = (im.format or "").upper()
        ext, mime = _FORMAT_TO_EXT_MIME.get(fmt, (None, None))
        if ext and mime:
            return f"{image_path.stem}.{ext}", mime
    except Exception:
        pass

    # Fallback: guess from path suffix
    content_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    return image_path.name, content_type


def login_admin(client: httpx.Client, email: str, password: str) -> None:
    csrf_resp = client.get("/api/v1/auth/csrf-token")
    _require_success(csrf_resp, "Fetch CSRF token")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    payload = _require_success(login_resp, "Admin login")
    user = (payload.get("data") or {}).get("user") or {}
    role = user.get("role")
    if role != "admin":
        raise ApiError(f"Authenticated user is not admin (role={role!r})")


def get_category_ids(client: httpx.Client) -> Dict[str, int]:
    resp = client.get("/api/v1/categories")
    payload = _require_success(resp, "Fetch categories")
    categories = payload.get("data") or []

    slug_to_id: Dict[str, int] = {}
    for category in categories:
        slug = str(category.get("slug", "")).strip().lower()
        cat_id = category.get("id")
        if slug and isinstance(cat_id, int):
            slug_to_id[slug] = cat_id

    # If the expected top-level categories don't exist yet, create them via admin API.
    missing = [slug for slug in ("men", "women", "kids") if slug not in slug_to_id]
    if missing:
        for slug in missing:
            name = slug.capitalize()
            create_resp = client.post(
                "/api/v1/admin/categories",
                data={"name": name, "description": f"Auto-created category: {name}"},
                headers=_csrf_headers(client),
            )
            # 409 means already exists (race/previous run); treat as ok.
            if create_resp.status_code not in (200, 201, 409):
                raise ApiError(
                    f"Failed to create missing category '{slug}' ({create_resp.status_code}): "
                    f"{_json_or_text(create_resp)}"
                )

        # Re-fetch categories after creation.
        resp = client.get("/api/v1/categories")
        payload = _require_success(resp, "Fetch categories (after creating missing)")
        categories = payload.get("data") or []

        slug_to_id = {}
        for category in categories:
            slug = str(category.get("slug", "")).strip().lower()
            cat_id = category.get("id")
            if slug and isinstance(cat_id, int):
                slug_to_id[slug] = cat_id

        missing = [slug for slug in ("men", "women", "kids") if slug not in slug_to_id]
        if missing:
            raise ApiError(
                "Required categories not found even after creation. Missing slugs: "
                + ", ".join(missing)
            )

    return slug_to_id


def create_product(
    client: httpx.Client,
    *,
    name: str,
    category_id: int,
    base_price: int,
    description: str,
    image_path: Path,
) -> Dict[str, Any]:
    if not image_path.exists():
        raise ApiError(f"Image file not found: {image_path}")

    upload_name, content_type = _detect_filename_and_content_type(image_path)

    data = {
        "name": name,
        "category_id": str(category_id),
        "description": description,
        "base_price": str(base_price),
        "is_featured": "false",
    }

    with image_path.open("rb") as fh:
        files = [("images", (upload_name, fh, content_type))]
        resp = client.post(
            "/api/v1/admin/products",
            data=data,
            files=files,
            headers=_csrf_headers(client),
        )

    payload = _require_success(resp, f"Create product '{name}'")
    data_obj = payload.get("data") or {}
    product_id = data_obj.get("product_id")
    slug = data_obj.get("slug")

    if not isinstance(product_id, int) or not slug:
        raise ApiError(f"Unexpected create-product response for '{name}': {payload}")

    return {"id": product_id, "slug": str(slug)}


def ensure_product_active(client: httpx.Client, product_id: int) -> None:
    resp = client.put(
        f"/api/v1/admin/products/{product_id}",
        data={"is_active": "true"},
        headers=_csrf_headers(client),
    )
    _require_success(resp, f"Set is_active=true for product {product_id}")


def add_variant(
    client: httpx.Client,
    *,
    product_id: int,
    size: str,
    color: str,
    stock_quantity: int,
) -> Dict[str, Any]:
    resp = client.post(
        f"/api/v1/admin/products/{product_id}/variants",
        data={
            "size": size,
            "color": color,
            "stock_quantity": str(stock_quantity),
            "additional_price": "0",
        },
        headers=_csrf_headers(client),
    )
    payload = _require_success(resp, f"Add variant for product {product_id}")
    sku = ((payload.get("data") or {}).get("sku"))
    return {"sku": sku}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create men/women/kids products via AMZIRA backend admin API"
    )
    parser.add_argument("--base-url", default=os.getenv("AMZIRA_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--admin-email", default=os.getenv("AMZIRA_ADMIN_EMAIL", "admin@amzira.com"))
    parser.add_argument("--admin-password", default=os.getenv("AMZIRA_ADMIN_PASSWORD", "CHANGE_ME"))

    parser.add_argument("--men-image", required=True, help="Path to image file for men product")
    parser.add_argument("--women-image", required=True, help="Path to image file for women product")
    parser.add_argument("--kids-image", required=True, help="Path to image file for kids product")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.admin_password == "CHANGE_ME":
        print("ERROR: Set --admin-password or AMZIRA_ADMIN_PASSWORD", file=sys.stderr)
        return 2

    image_paths = {
        "men": Path(args.men_image).expanduser().resolve(),
        "women": Path(args.women_image).expanduser().resolve(),
        "kids": Path(args.kids_image).expanduser().resolve(),
    }

    products_plan: List[Dict[str, Any]] = [
        {
            "category_slug": "men",
            "name": "Men Classic Sherwani Set",
            "price": 4499,
            "description": "Simple men sherwani set for catalog seeding.",
            "size": "M",
            "color": "Beige",
            "stock": 10,
        },
        {
            "category_slug": "women",
            "name": "Women Festive Kurta Set",
            "price": 3299,
            "description": "Simple women festive kurta set for catalog seeding.",
            "size": "S",
            "color": "Rose",
            "stock": 12,
        },
        {
            "category_slug": "kids",
            "name": "Kids Ethnic Kurta Set",
            "price": 1999,
            "description": "Simple kids ethnic set for catalog seeding.",
            "size": "6Y",
            "color": "Cream",
            "stock": 8,
        },
    ]

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0, follow_redirects=True) as client:
        login_admin(client, args.admin_email, args.admin_password)
        category_ids = get_category_ids(client)

        print("Created products:")
        for item in products_plan:
            category_slug = item["category_slug"]
            created = create_product(
                client,
                name=item["name"],
                category_id=category_ids[category_slug],
                base_price=item["price"],
                description=item["description"],
                image_path=image_paths[category_slug],
            )
            ensure_product_active(client, created["id"])
            variant = add_variant(
                client,
                product_id=created["id"],
                size=item["size"],
                color=item["color"],
                stock_quantity=item["stock"],
            )

            print(
                f"- {category_slug}: id={created['id']}, slug={created['slug']}, sku={variant.get('sku')}"
            )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

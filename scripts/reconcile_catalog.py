"""Reconcile the live catalog against database, media, cache, and sitemap invariants.

Run this from a trusted operator environment with production read access. It is
read-only; it never changes products, stock, Redis, or the sitemap.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy import func

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.cache import get_redis  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Product, ProductVariant  # noqa: E402


FRONT_VIEW_RE = re.compile(r"(?:^|[^a-z])front(?:[^a-z]|$)", re.IGNORECASE)


def _is_front(image) -> bool:
    # The catalog importer marks the canonical first image as primary, while
    # older rows may use either "Front View" or "front_view" in alt text.
    return bool(
        image.is_primary
        or FRONT_VIEW_RE.search(f"{image.alt_text or ''} {image.image_url or ''}")
    )


def _validated_http_url(url: str, *, allowed_host: str | None = None) -> str:
    parsed = urlsplit(str(url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must be an HTTP(S) URL without embedded credentials")
    if allowed_host is not None and parsed.hostname.lower() != allowed_host.lower():
        raise ValueError("URL host does not match the configured public media host")
    return parsed.geturl()


def _configured_media_host() -> str:
    parsed = urlsplit(settings.R2_PUBLIC_URL.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("R2_PUBLIC_URL must be a valid HTTPS URL")
    return parsed.hostname


def _redacted_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<invalid>"
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    except (TypeError, ValueError):
        return "<invalid>"


def _head(url: str, *, allowed_host: str) -> tuple[bool, str]:
    try:
        target = _validated_http_url(url, allowed_host=allowed_host)
        response = httpx.head(
            target,
            timeout=10,
            follow_redirects=False,
            headers={"User-Agent": "amzira-catalog-reconciler/1.0"},
        )
        return 200 <= response.status_code < 400, str(response.status_code)
    except (httpx.HTTPError, ValueError) as exc:
        return False, type(exc).__name__


def _sitemap_slugs(url: str) -> tuple[set[str], str | None]:
    try:
        target = _validated_http_url(url)
        response = httpx.get(
            target,
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "amzira-catalog-reconciler/1.0"},
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        locs = {
            element.text.rstrip("/").rsplit("/product/", 1)[-1]
            for element in root.iter()
            if element.tag.endswith("loc") and element.text and "/product/" in element.text
        }
        return locs, None
    except (
        httpx.HTTPError,
        DefusedXmlException,
        ElementTree.ParseError,
        ValueError,
    ) as exc:
        return set(), f"Sitemap check failed: {type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-stock", type=int, default=21)
    parser.add_argument("--max-stock", type=int, default=50)
    parser.add_argument("--check-media", action="store_true")
    parser.add_argument("--sitemap-url", default="https://www.amzira.com/sitemap.xml")
    parser.add_argument("--skip-sitemap", action="store_true")
    parser.add_argument("--check-redis", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    media_failures: list[dict[str, str]] = []
    media_host: str | None = None
    if args.check_media:
        try:
            media_host = _configured_media_host()
        except ValueError as exc:
            errors.append(str(exc))
    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.is_active == True).all()
        product_ids = [product.id for product in products]
        variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.product_id.in_(product_ids), ProductVariant.is_active == True)
            .all()
            if product_ids
            else []
        )
        active_count = len(products)
        variant_count = len(variants)
        stock_values = [int(variant.stock_quantity or 0) for variant in variants]
        image_count = sum(len(product.images) for product in products)

        if not products:
            errors.append("No active products found")
        if not variants:
            errors.append("No active variants found")

        for product in products:
            ordered_images = sorted(product.images, key=lambda image: (image.display_order or 0, image.id))
            front_images = [image for image in ordered_images if _is_front(image)]
            if not ordered_images:
                errors.append(f"{product.slug}: no images")
            elif not front_images:
                errors.append(f"{product.slug}: no front-view image")
            elif ordered_images[0].id != front_images[0].id:
                errors.append(f"{product.slug}: first image is not the front view")

            for image in ordered_images:
                if args.check_media and media_host:
                    ok, status = _head(image.image_url, allowed_host=media_host)
                    if not ok:
                        media_failures.append(
                            {"url": _redacted_url(image.image_url), "status": status}
                        )

        out = {
            "status": "ok",
            "environment": settings.ENVIRONMENT,
            "active_products": active_count,
            "active_variants": variant_count,
            "stock_min": min(stock_values) if stock_values else None,
            "stock_max": max(stock_values) if stock_values else None,
            "stock_total": sum(stock_values),
            "images": image_count,
            "media_failures": media_failures,
            "sitemap": None,
            "redis": None,
            "errors": errors,
        }

        if stock_values and (min(stock_values) < args.min_stock or max(stock_values) > args.max_stock):
            errors.append(f"Stock outside configured range {args.min_stock}-{args.max_stock}")
        if media_failures:
            errors.append(f"{len(media_failures)} media URLs failed")

        if not args.skip_sitemap:
            sitemap_products, sitemap_error = _sitemap_slugs(args.sitemap_url)
            missing = sorted(set(product.slug for product in products) - sitemap_products)
            out["sitemap"] = {"url": args.sitemap_url, "product_urls": len(sitemap_products), "missing": missing}
            if sitemap_error:
                errors.append(sitemap_error)
            elif missing:
                errors.append(f"{len(missing)} active products missing from sitemap")

        if args.check_redis:
            redis = get_redis()
            if redis is None:
                errors.append("Redis check requested but Redis is unavailable")
            else:
                stale_keys: list[dict[str, object]] = []
                for key in redis.scan_iter(match="cache:products:count:*"):
                    raw = redis.get(key)
                    try:
                        cached = int(json.loads(raw)) if raw is not None else None
                    except (TypeError, ValueError, json.JSONDecodeError):
                        cached = None
                    if cached is None:
                        stale_keys.append({"key": key, "value": raw})
                out["redis"] = {"count_keys": len(list(redis.scan_iter(match="cache:products:count:*"))), "invalid_keys": stale_keys}
                if stale_keys:
                    errors.append(f"{len(stale_keys)} invalid product-count cache entries")

        out["errors"] = errors
        out["status"] = "failed" if errors else "ok"
        print(json.dumps(out, indent=2, sort_keys=True))
        return 1 if errors else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

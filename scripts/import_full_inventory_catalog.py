"""Validate or apply the generated full local inventory catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.cache import invalidate_product_cache
from app.db.session import SessionLocal
from app.schemas.catalog_import import CatalogImportRequest
from app.services.catalog_import_service import CatalogImportValidationError, import_catalog


DEFAULT_CATALOG = ROOT / "build/catalog-full-inventory-local.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--apply", action="store_true", help="Commit the atomic upsert; default is a dry run")
    parser.add_argument("--expected-products", type=int, default=None)
    args = parser.parse_args()

    raw_payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    raw_payload["mode"] = "upsert"
    raw_payload["dry_run"] = not args.apply
    payload = CatalogImportRequest.model_validate(raw_payload)
    if args.expected_products is not None and len(payload.products) != args.expected_products:
        raise SystemExit(f"Expected {args.expected_products} products, found {len(payload.products)}")

    db = SessionLocal()
    try:
        report = import_catalog(db, payload)
    except CatalogImportValidationError as exc:
        print(json.dumps({"status": "rejected", "errors": exc.errors}, indent=2))
        raise SystemExit(1) from exc
    finally:
        db.close()

    if args.apply:
        invalidate_product_cache(slugs=report.get("changed_slugs"))
    print(json.dumps({"status": "applied" if args.apply else "accepted", **report}, indent=2))


if __name__ == "__main__":
    main()

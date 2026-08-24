"""Run the launch CSV through the production parser and atomic importer in dry-run mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.v1.admin import _catalog_request_from_csv
from app.services.catalog_import_service import CatalogImportValidationError, import_catalog


DEFAULT_CSV = ROOT / "docs/catalog-launch-work-haresh.csv"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--apply", action="store_true", help="Apply the import; default is a dry run")
    args = parser.parse_args()

    payload = _catalog_request_from_csv(args.catalog.read_bytes(), mode="upsert", dry_run=not args.apply)
    engine = create_engine(args.database_url)
    session = sessionmaker(bind=engine)()
    try:
        report = import_catalog(session, payload)
    except CatalogImportValidationError as exc:
        print(json.dumps({"status": "rejected", "rejected": len(exc.errors), "errors": exc.errors}, indent=2))
        raise SystemExit(1) from exc
    finally:
        session.close()
        engine.dispose()

    accepted = report["products_received"]
    rejected = len(report["errors"])
    if accepted != 27 or rejected != 0 or report["dry_run"] == args.apply:
        raise SystemExit(f"Unexpected dry-run report: {report}")
    print(json.dumps({"status": "accepted", "accepted_products": accepted, "rejected_products": rejected, **report}, indent=2))


if __name__ == "__main__":
    main()

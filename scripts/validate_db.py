#!/usr/bin/env python3
"""
Validate PostgreSQL e-commerce schema requirements.

Usage:
  python3 scripts/validate_db.py

Connection defaults:
  DB host: localhost
  DB port: 5432
  DB name: amzira_db
  DB user: postgres
  DB pass: (empty)

Preferred env override:
  DATABASE_URL (e.g., postgresql://postgres@localhost:5432/amzira_db)

Fallback env override:
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str


def env_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def get_engine() -> Engine:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return create_engine(database_url, future=True)

    host = env_or_default("PGHOST", "localhost")
    port = env_or_default("PGPORT", "5432")
    dbname = env_or_default("PGDATABASE", "amzira_db")
    user = env_or_default("PGUSER", "postgres")
    password = env_or_default("PGPASSWORD", "")
    if password:
        url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
    else:
        url = f"postgresql+psycopg2://{user}@{host}:{port}/{dbname}"
    return create_engine(url, future=True)


def format_result(result: CheckResult) -> str:
    status = "PASS" if result.passed else "FAIL"
    return f"[{status}] {result.name} - {result.details}"


def unique_on_column(engine: Engine, table: str, column: str) -> CheckResult:
    sql = text(
        """
        SELECT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_class t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
          JOIN pg_catalog.pg_index i ON i.indrelid = t.oid
          JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
          WHERE t.relkind = 'r'
            AND n.nspname = current_schema()
            AND t.relname = :table
            AND a.attname = :column
            AND a.attnum = ANY(i.indkey)
            AND i.indisunique = TRUE
            AND i.indnatts = 1
        ) AS is_unique
        """
    )
    with engine.connect() as conn:
        is_unique = bool(conn.execute(sql, {"table": table, "column": column}).scalar())
    if is_unique:
        return CheckResult(
            name=f"{table}.{column} is UNIQUE",
            passed=True,
            details="unique index/constraint present",
        )
    return CheckResult(
        name=f"{table}.{column} is UNIQUE",
        passed=False,
        details="no unique index/constraint on the column",
    )


def check_constraint_stock_quantity(engine: Engine) -> CheckResult:
    sql = text(
        """
        SELECT conname, pg_get_constraintdef(c.oid) AS def
        FROM pg_catalog.pg_constraint c
        JOIN pg_catalog.pg_class t ON t.oid = c.conrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype = 'c'
          AND n.nspname = current_schema()
          AND t.relname = 'product_variants'
          AND pg_get_constraintdef(c.oid) ILIKE '%stock_quantity%'
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()

    for row in rows:
        definition = row._mapping.get("def") or ""
        normalized = " ".join(definition.lower().split())
        if "stock_quantity" in normalized and ">=" in normalized and "0" in normalized:
            return CheckResult(
                name="product_variants.stock_quantity CHECK >= 0",
                passed=True,
                details=f"constraint {row._mapping.get('conname')} present: {definition}",
            )

    if rows:
        return CheckResult(
            name="product_variants.stock_quantity CHECK >= 0",
            passed=False,
            details="check constraint exists but does not enforce >= 0",
        )
    return CheckResult(
        name="product_variants.stock_quantity CHECK >= 0",
        passed=False,
        details="no check constraint on stock_quantity",
    )


def table_and_columns_exist(engine: Engine) -> CheckResult:
    inspector = inspect(engine)
    table = "product_images"
    if not inspector.has_table(table):
        return CheckResult(
            name="product_images table/columns",
            passed=False,
            details="table product_images is missing",
        )

    columns = {col["name"] for col in inspector.get_columns(table)}
    required = {"id", "product_id", "image_url", "is_primary"}
    missing = required - columns
    if missing:
        return CheckResult(
            name="product_images table/columns",
            passed=False,
            details=f"missing columns: {', '.join(sorted(missing))}",
        )

    pk = inspector.get_pk_constraint(table) or {}
    pk_cols = set(pk.get("constrained_columns") or [])
    if "id" not in pk_cols:
        return CheckResult(
            name="product_images table/columns",
            passed=False,
            details="id is not a primary key",
        )

    fks = inspector.get_foreign_keys(table) or []
    fk_ok = False
    for fk in fks:
        if (
            fk.get("referred_table") == "products"
            and "product_id" in (fk.get("constrained_columns") or [])
            and "id" in (fk.get("referred_columns") or [])
        ):
            fk_ok = True
            break
    if not fk_ok:
        return CheckResult(
            name="product_images table/columns",
            passed=False,
            details="missing FK product_images.product_id -> products.id",
        )

    return CheckResult(
        name="product_images table/columns",
        passed=True,
        details="table, columns, PK, and FK are present",
    )


def default_true(engine: Engine, table: str, column: str) -> CheckResult:
    sql = text(
        """
        SELECT column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = :table
          AND column_name = :column
        """
    )
    with engine.connect() as conn:
        default = conn.execute(sql, {"table": table, "column": column}).scalar()

    if default is None:
        return CheckResult(
            name=f"{table}.{column} default TRUE",
            passed=False,
            details="no default set",
        )

    normalized = " ".join(str(default).lower().split())
    if "true" in normalized:
        return CheckResult(
            name=f"{table}.{column} default TRUE",
            passed=True,
            details=f"default is {default}",
        )

    return CheckResult(
        name=f"{table}.{column} default TRUE",
        passed=False,
        details=f"default is {default} (expected TRUE)",
    )


def run_checks(engine: Engine) -> Iterable[CheckResult]:
    yield unique_on_column(engine, "products", "slug")
    yield unique_on_column(engine, "categories", "slug")
    yield unique_on_column(engine, "subcategories", "slug")
    yield unique_on_column(engine, "product_variants", "sku")
    yield check_constraint_stock_quantity(engine)
    yield table_and_columns_exist(engine)
    yield default_true(engine, "categories", "is_active")
    yield default_true(engine, "subcategories", "is_active")
    yield default_true(engine, "products", "is_active")


def main() -> int:
    try:
        engine = get_engine()
    except Exception as exc:
        print(f"[FAIL] database connection setup - {exc}")
        return 2

    results = []
    try:
        for result in run_checks(engine):
            results.append(result)
            print(format_result(result))
    except Exception as exc:
        print(f"[FAIL] validation error - {exc}")
        return 2

    if all(r.passed for r in results):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

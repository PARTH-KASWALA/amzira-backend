#!/usr/bin/env bash

set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL must target an empty PostgreSQL database}"

PG_RESTORE_DATABASE_URL="${RESTORE_DATABASE_URL/+psycopg2/}"

# Render PostgreSQL requires TLS. Keep local development/restore drills usable
# without TLS while enforcing it for every non-local database URL.
if [[ "${PG_RESTORE_DATABASE_URL}" != *"sslmode="* && "${PG_RESTORE_DATABASE_URL}" != *"localhost"* && "${PG_RESTORE_DATABASE_URL}" != *"127.0.0.1"* && "${PG_RESTORE_DATABASE_URL}" != *"[::1]"* ]]; then
  if [[ "${PG_RESTORE_DATABASE_URL}" == *"?"* ]]; then
    PG_RESTORE_DATABASE_URL="${PG_RESTORE_DATABASE_URL}&sslmode=require"
  else
    PG_RESTORE_DATABASE_URL="${PG_RESTORE_DATABASE_URL}?sslmode=require"
  fi
fi

BACKUP_FILE="${1:-}"
if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  printf 'Usage: RESTORE_DATABASE_URL=postgresql://... %s /path/to/amzira.dump\n' "$0" >&2
  exit 2
fi

if [[ -f "${BACKUP_FILE}.sha256" ]]; then
  shasum -a 256 --check "${BACKUP_FILE}.sha256"
fi

pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl \
  --dbname="${PG_RESTORE_DATABASE_URL}" "${BACKUP_FILE}"

printf 'Restore completed from: %s\n' "${BACKUP_FILE}"

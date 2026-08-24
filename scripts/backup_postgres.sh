#!/usr/bin/env bash

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must contain the PostgreSQL connection URL}"

PG_DATABASE_URL="${DATABASE_URL/+psycopg2/}"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/amzira-${TIMESTAMP}.dump"

mkdir -p "${BACKUP_DIR}"
pg_dump --format=custom --no-owner --no-acl --file="${BACKUP_FILE}" "${PG_DATABASE_URL}"
shasum -a 256 "${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"
find "${BACKUP_DIR}" -type f \( -name 'amzira-*.dump' -o -name 'amzira-*.dump.sha256' \) \
  -mtime "+${RETENTION_DAYS}" -delete

printf 'Backup created: %s\n' "${BACKUP_FILE}"

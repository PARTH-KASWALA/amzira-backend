# Backup Strategy

## Automated Backup
- Schedule `pg_dump` for the production database at least once daily.
- Command pattern:
  - `pg_dump -Fc -h <host> -U <user> -d <database> -f /backups/amzira_YYYYMMDD_HHMM.dump`
- Keep backups in immutable object storage and local encrypted storage.
- Rotation policy:
  - Daily backups: keep 14 days
  - Weekly backups: keep 8 weeks
  - Monthly backups: keep 12 months
- Validate backup integrity weekly with a test restore.

## Manual Backup
- Before major deployments, schema migrations, or data backfills:
  - `pg_dump -Fc -h <host> -U <user> -d <database> -f /backups/manual_predeploy_YYYYMMDD_HHMM.dump`
- Confirm file size is non-zero and checksum is recorded.
- Upload manual snapshots to offsite encrypted storage.

## Restore Procedure
1. Identify the target backup file and verify checksum.
2. Provision a restore database instance with matching PostgreSQL version.
3. Restore:
   - `pg_restore -c -h <host> -U <user> -d <target_database> /backups/<backup_file>.dump`
4. Run smoke checks:
   - DB connectivity
   - Auth login flow
   - Order creation flow
5. If restoring production, place API in maintenance mode during cutover.
6. Re-enable API traffic and monitor error logs and health endpoints.

## Disaster Recovery Checklist
- Incident declared and severity confirmed.
- Last known good backup identified.
- Restore environment provisioned and access restricted.
- Backup restore completed and validated.
- Application health checks passing (`/health`, `/health/database`, `/health/email`).
- Data integrity spot checks completed (users, products, orders, payments).
- Post-incident report documented with timeline and preventive actions.

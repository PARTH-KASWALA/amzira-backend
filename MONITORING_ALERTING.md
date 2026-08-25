# AMZIRA Production Monitoring And Alerting

The API emits JSON structured logs, correlation/request IDs, slow-request
signals, payment/webhook events, stock events, and Celery task failures. Render
log drains or an equivalent sink must retain these logs and route the alert
rules below to the on-call owner.

## Required instrumentation

- Sentry: set `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, and `GIT_COMMIT` on the
  API. Verify that a deliberate staging exception appears with the deployment
  release and no customer PII.
- API logs: alert on `request_health_signal` with `status_code >= 500`, and on
  `duration_ms >= SLOW_REQUEST_THRESHOLD_MS`.
- Commerce logs: alert on `payment_failed`, `payment_signature_mismatch`,
  webhook validation failures, `stock_depleted`, and order reservation expiry.
- Celery: monitor `/health/email`, `/health/email-queue`, worker heartbeats,
  task retries, and queue depth. Alert when there are no workers or the email
  queue grows for 10 minutes.
- Database: monitor `/health/database`, connection-pool saturation, migration
  failures, and the managed PostgreSQL backup/restore job.

## Alert thresholds

| Signal | Warning | Critical | Response |
| --- | --- | --- | --- |
| HTTP 5xx ratio | >1% for 5 min | >5% for 5 min | Check deploy, Sentry, DB, rollback if needed |
| p95 API latency | >1.0 s for 10 min | >2.0 s for 5 min | Inspect slow-request logs and DB queries |
| Payment failures | 5 in 10 min | 10 in 10 min | Check Razorpay status, signatures, and webhook delivery |
| Queue depth | >25 for 10 min | >100 for 10 min | Check worker health and Redis |
| Backup | one missed run | two missed runs | Restore-test the latest valid dump |

## Operator checks

- Liveness: `GET /health`
- Database: authenticated `GET /health/database`
- Workers: authenticated `GET /health/email`
- Queue: authenticated `GET /health/email-queue`
- Catalog: authenticated `GET /health/catalog-launch`
- Catalog reconciliation: `python scripts/reconcile_catalog.py --check-media --check-redis`

The application cannot create provider-side alerts or backups by itself. The
Render, Sentry, Razorpay, Shiprocket, Redis, and PostgreSQL dashboards must be
configured and verified before public checkout is enabled.

## Health Endpoints
- `/health` for API liveness
- `/health/database` for DB connectivity/pool
- `/health/email` for worker connectivity
- `/health/email-queue` for queue depth visibility

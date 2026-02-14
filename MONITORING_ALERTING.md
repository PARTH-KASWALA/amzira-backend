# Monitoring And Alerting Scaffold

## Observability Placeholders
- APM integration placeholder:
  - Add OpenTelemetry/New Relic/Datadog instrumentation in app startup.
  - Track request latency, DB query timings, Celery task durations.
- Log aggregation placeholder:
  - Forward structured logs to a centralized sink (ELK/Datadog/CloudWatch).
  - Retain logs with index templates for `payment_failed`, `stock_depleted`, `order_expired`.

## Alerting Placeholders
- Error-rate alerts:
  - Trigger alert when 5xx error ratio exceeds baseline for 5 minutes.
  - Trigger warning for sustained 4xx spikes.
- Payment failure alerts:
  - Trigger alert on burst of `payment_failed` events.
  - Trigger warning on repeated `payment_signature_mismatch` events.

## Health Endpoints
- `/health` for API liveness
- `/health/database` for DB connectivity/pool
- `/health/email` for worker connectivity
- `/health/email-queue` for queue depth visibility

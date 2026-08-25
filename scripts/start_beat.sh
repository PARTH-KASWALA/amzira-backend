#!/bin/sh
set -eu

python scripts/wait_for_migrations.py
exec python -m celery -A app.core.celery_app:celery_app beat \
  --loglevel=INFO \
  --schedule=/tmp/celerybeat-schedule

#!/bin/sh
set -e

alembic upgrade head
python -m app.db.init_db

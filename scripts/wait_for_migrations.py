"""Block workers until the database reaches the repository's Alembic head."""

from __future__ import annotations

import os
import time

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    deadline = time.monotonic() + 600
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        while time.monotonic() < deadline:
            try:
                with engine.connect() as connection:
                    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
                if current == expected_head:
                    print(f"Database migrations ready at {current}")
                    return
            except SQLAlchemyError:
                pass
            time.sleep(5)
    finally:
        engine.dispose()
    raise SystemExit(f"Database did not reach Alembic head {expected_head} within 10 minutes")


if __name__ == "__main__":
    main()

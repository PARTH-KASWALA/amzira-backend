import os

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.models.order import Order, OrderStatus


def test_order_status_model_persists_public_lowercase_values():
    assert Order.__table__.c.status.type.enums == [status.value for status in OrderStatus]


@pytest.mark.skipif(
    os.getenv("AMZIRA_RUN_POSTGRES_ENUM_TEST") != "1",
    reason="Set AMZIRA_RUN_POSTGRES_ENUM_TEST=1 to verify the migrated PostgreSQL enum",
)
def test_postgres_order_status_enum_matches_model():
    engine = create_engine(settings.DATABASE_URL)
    try:
        with engine.connect() as connection:
            labels = connection.execute(
                text(
                    """
                    SELECT enumlabel
                    FROM pg_enum
                    JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                    WHERE pg_type.typname = 'orderstatus'
                    ORDER BY enumsortorder
                    """
                )
            ).scalars().all()
        assert labels == [status.value for status in OrderStatus]
    finally:
        engine.dispose()

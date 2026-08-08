import os
import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ["ENVIRONMENT"] = "development"
os.environ["TESTING"] = "true"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["CELERY_TASK_STORE_EAGER_RESULT"] = "false"

import app.models  # noqa: F401
import app.models.return_request  # noqa: F401
from app.core.config import settings
from app.db.base_class import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()

    engine = create_engine(
        f"sqlite:///{db_file.name}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_tables = list(Base.metadata.sorted_tables)
    Base.metadata.create_all(bind=engine, tables=test_tables)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=test_tables)
        engine.dispose()
        os.unlink(db_file.name)


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    original_shiprocket_email = settings.SHIPROCKET_EMAIL
    original_shiprocket_password = settings.SHIPROCKET_PASSWORD
    settings.SHIPROCKET_EMAIL = ""
    settings.SHIPROCKET_PASSWORD = ""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.state.limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    settings.SHIPROCKET_EMAIL = original_shiprocket_email
    settings.SHIPROCKET_PASSWORD = original_shiprocket_password

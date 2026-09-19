import os

# Must be set before `app.config` (and anything importing it) is loaded for
# the first time, so tests never need a real Voyage API key or a file-backed
# database.
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("VOYAGE_API_KEY", "unused-in-tests")

# Fixed test admin account. Hash corresponds to ADMIN_TEST_PASSWORD below —
# regenerate both together with scripts/hash_admin_password.py if changed.
ADMIN_TEST_USERNAME = "test-admin"
ADMIN_TEST_PASSWORD = "test-admin-password"
os.environ.setdefault("ADMIN_USERNAME", ADMIN_TEST_USERNAME)
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    "$2b$12$kImu/gKz8a20/Qd3R/7hJupK82Xxdvzl7Z/EuLM9mhjS8rbm61CYq",
)
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-key-not-for-production")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    """Wrong-password counters are per process; every test starts unlocked."""
    from app import security

    security.login_throttle = security.LoginThrottle()


@pytest.fixture(autouse=True)
def _forget_last_rescore():
    """rescore_recent skips when the thumbs look unchanged since its last call
    in this process; every test starts with its own database."""
    from app import scoring

    scoring._last_rescore_signature = None


def _make_test_engine():
    """SQLite by default (fast, no server needed); a real Postgres engine when
    DATABASE_URL is pointed at one (e.g. docker compose / the VM deployment),
    so the exact same test suite exercises the pgvector-backed embedding
    column instead of the JSON-text SQLite fallback.
    """
    if settings.database_url.startswith("sqlite"):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(settings.database_url)


@pytest.fixture()
def db_session():
    engine = _make_test_engine()
    is_sqlite = settings.database_url.startswith("sqlite")
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        if not is_sqlite:
            # Postgres is a persistent server (unlike the in-memory SQLite
            # engine, which is discarded after every test): drop everything
            # so each test starts from a clean, empty schema.
            Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client(client):
    resp = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD},
    )
    assert resp.status_code == 200  # followed the post-login redirect to /admin/sources
    return client

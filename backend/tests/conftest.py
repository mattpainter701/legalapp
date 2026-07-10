"""
Test fixtures for Clarity Legal backend.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User

settings = get_settings()

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/legalapp_test",
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


def pytest_collection_modifyitems(items):
    """Pin every async test to the session-scoped event loop.

    ``pytest.ini`` runs async *fixtures* on a session-scoped loop, but
    pytest-asyncio 0.24 has no ini option for the *test* loop scope, so tests
    default to a per-function loop. The mismatch makes asyncpg connections
    (bound to the fixtures' session loop) unusable from the test loop. Applying
    ``loop_scope="session"`` to all asyncio tests keeps tests and fixtures on
    the same loop.
    """
    session_marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if "asyncio" in item.keywords:
            # Replace (not stack) any existing asyncio marker so the session
            # loop_scope is the one pytest-asyncio actually reads.
            item.own_markers = [m for m in item.own_markers if m.name != "asyncio"]
            item.add_marker(session_marker)


def _normalize_server_defaults() -> None:
    """Make ``Base.metadata.create_all`` usable for tests.

    Several models declare SQL function defaults as plain strings (e.g.
    ``server_default="gen_random_uuid()"`` / ``"now()"``). SQLAlchemy renders a
    bare string as a *quoted literal*, so ``CREATE TABLE`` emits
    ``DEFAULT 'gen_random_uuid()'`` which Postgres rejects. Production builds
    its schema via Alembic (which wraps these in ``sa.text(...)``), so this only
    bites the test ``create_all`` path. Wrap any function-call default in
    ``text()`` so the DDL renders as raw SQL.
    """
    from sqlalchemy import text as _text
    from sqlalchemy.sql.elements import TextClause

    for table in Base.metadata.tables.values():
        for column in table.columns:
            default = column.server_default
            arg = getattr(default, "arg", None)
            if (
                isinstance(arg, str)
                and arg.strip().endswith(")")
                and not isinstance(arg, TextClause)
            ):
                default.arg = _text(arg)


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    from sqlalchemy import text as _text

    _normalize_server_defaults()
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        # pgvector (chunks.embedding) + pgcrypto (gen_random_uuid) — present in
        # prod via migrations; ensure they exist for the test schema too.
        await conn.execute(_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(_text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    # DROP SCHEMA CASCADE rather than metadata.drop_all — the latter can't
    # topologically sort the pre-existing invoices<->retainers FK cycle.
    async with engine.begin() as conn:
        await conn.execute(_text("DROP SCHEMA public CASCADE"))
        await conn.execute(_text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def test_redis():
    """Provide the application middleware with an isolated, real Redis DB.

    ``ASGITransport`` does not execute the FastAPI lifespan that normally
    installs Redis.  Using database 15 keeps rate-limit, revocation, and OAuth
    state behavior production-like without sharing application data.
    """

    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.ping()
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    from sqlalchemy import text as _text

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    # Per-test isolation: fixtures (test_tenant/test_user) and handlers commit
    # data, and the engine is session-scoped, so wipe all tables before each
    # test to avoid cross-test contamination (e.g. duplicate tenant domains).
    table_list = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with factory() as session:
        if table_list:
            # The ApiAccessLogMiddleware writes api_access_logs on its own
            # session and commits asynchronously, so its INSERT can still hold a
            # RowShareLock when this TRUNCATE asks for AccessExclusiveLock —
            # Postgres then reports a deadlock and kills one side. Retry the
            # TRUNCATE a few times so the reset wins once the stray write clears.
            stmt = _text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")
            for attempt in range(10):
                try:
                    # Never let one leaked/background test connection wedge the
                    # hosted suite indefinitely. PostgreSQL otherwise waits
                    # forever for TRUNCATE's AccessExclusiveLock. SET LOCAL is
                    # transaction-scoped, so re-apply it after every rollback.
                    await session.execute(_text("SET LOCAL lock_timeout = '2s'"))
                    await session.execute(stmt)
                    await session.commit()
                    break
                except DBAPIError as exc:
                    await session.rollback()
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                    retryable_lock = (
                        sqlstate in {"40P01", "55P03"}
                        or "deadlock" in str(exc).lower()
                        or "lock timeout" in str(exc).lower()
                    )
                    if not retryable_lock or attempt == 9:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_tenant(db_session: AsyncSession):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Test Law Firm",
        domain="testfirm.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession, test_tenant: Tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="attorney@testfirm.com",
        full_name="Test Attorney",
        role="admin",
        oauth_provider="google",
        oauth_subject="google-sub-123",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_token(test_user: User, test_tenant: Tenant):
    from jose import jwt as jose_jwt

    payload = {
        "sub": str(test_user.id),
        "tenant_id": str(test_tenant.id),
        "role": test_user.role,
        "email": test_user.email,
        "billing_tier": test_tenant.billing_tier,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, auth_token: str, test_redis):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    previous_redis = getattr(app.state, "redis", None)
    app.state.redis = test_redis

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {auth_token}"},
        ) as ac:
            yield ac
    finally:
        app.state.redis = previous_redis
        app.dependency_overrides.clear()


@pytest.fixture
def mock_llm():
    with patch("app.services.llm.LLMService.complete", new_callable=AsyncMock) as mock:
        mock.return_value = (
            "The court held in *Smith v. Jones*, 123 F.3d 456 (9th Cir. 2020) [settled] "
            "that the standard applies.\n\n*This is not legal advice. Please consult a qualified attorney.*",
            100,
            150,
        )
        yield mock


@pytest.fixture
def mock_embeddings():
    vec = [0.01] * 1536
    with patch(
        "app.services.embeddings.EmbeddingService.embed_text", new_callable=AsyncMock
    ) as mt:
        mt.return_value = vec
        with patch(
            "app.services.embeddings.EmbeddingService.embed_batch",
            new_callable=AsyncMock,
        ) as mb:
            mb.return_value = [vec]
            yield mt, mb

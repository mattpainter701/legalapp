"""
Test fixtures for Clarity Legal backend.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User

settings = get_settings()

TEST_DB_URL = (
    "postgresql+asyncpg://test:test@localhost:5432/legalapp_test"
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
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
async def client(db_session: AsyncSession, auth_token: str):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {auth_token}"},
    ) as ac:
        yield ac

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
    with patch("app.services.embeddings.EmbeddingService.embed_text", new_callable=AsyncMock) as mt:
        mt.return_value = vec
        with patch("app.services.embeddings.EmbeddingService.embed_batch", new_callable=AsyncMock) as mb:
            mb.return_value = [vec]
            yield mt, mb

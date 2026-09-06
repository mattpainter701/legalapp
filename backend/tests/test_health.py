import pytest
from httpx import ASGITransport, AsyncClient

from app import main as app_main
from app.main import app


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "dev"
    assert data["short_commit"] == "dev"
    assert "commit" in data
    assert "build_time" in data
    assert "status" in data
    assert "database" in data


@pytest.mark.asyncio
async def test_llm_health_degraded_response_does_not_expose_exception(
    monkeypatch, caplog
):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url):
            raise RuntimeError(
                "https://llm.internal.example/v1?api_key=secret-token "
                "provider=acme connection refused"
            )

    class FakeHTTPX:
        AsyncClient = FailingClient

    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", True)
    monkeypatch.setattr(
        app_main.settings, "LITELLM_BASE_URL", "https://llm.internal.example"
    )
    monkeypatch.setitem(__import__("sys").modules, "httpx", FakeHTTPX)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/llm")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded"}
    assert "secret-token" not in response.text
    assert "llm.internal.example" not in response.text
    assert "connection refused" not in response.text
    assert "LLM health check failed" in caplog.text


@pytest.mark.asyncio
async def test_llm_health_status_contract(monkeypatch):
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/llm")
    assert response.status_code == 200
    assert response.json() == {"status": "disabled"}


@pytest.mark.asyncio
async def test_readiness_ignores_synthetic_tenants_with_no_scheduler(db_session):
    """A synthetic tenant must not hold readiness down where no scheduler runs.

    dev1 deliberately starts no scheduler, so nothing ever writes a
    scheduler-heartbeat there. The seeded demo fixture tenant is created with
    billing_tier "fixture", which the customer boundary did not exclude, so
    readiness reported scheduler "stale" on every probe for four days.
    """
    import uuid

    from app.models.tenant import Tenant

    db_session.add(
        Tenant(
            id=uuid.uuid4(),
            name="LawHand Practice Demo - Synthetic",
            domain="lawhand-corporate-demo.invalid",
            billing_tier="fixture",
            is_active=True,
        )
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/readiness")

    components = response.json()["components"]
    assert components["scheduler"] == "ok"
    assert components["queue"] == "ok"

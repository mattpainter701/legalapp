"""Cloud setup retries must isolate each matter's database work."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.models.plugin import Matter
from app.services import cloud_init


@pytest.mark.asyncio
async def test_cloud_init_retry_keeps_later_matters_healthy_after_database_error(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """A failed savepoint must not turn every later matter into InFailedSQLTransaction."""
    first = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="first-matter",
        matter_name="First matter",
    )
    second = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="second-matter",
        matter_name="Second matter",
    )
    db_session.add_all([first, second])
    await db_session.commit()
    matter_ids = {first.id, second.id}
    attempts = []

    async def fake_root(_db, _tenant_id):
        return {"onedrive": {"id": "root-id"}}

    async def fake_tokens(_db, _tenant_id, _cloud_root):
        return {"microsoft": "fresh-token"}

    async def fake_matter_init(db, *, matter_id, tokens, **_kwargs):
        attempts.append((matter_id, tokens))
        if len(attempts) == 1:
            # This is a real Postgres error, so without a savepoint the session
            # would remain aborted and the next matter could not run. Matter
            # query order is intentionally unspecified by PostgreSQL.
            await db.execute(text("SELECT 1 / 0"))
        return {"onedrive": {"matter_folder_id": f"folder-{matter_id}"}}

    monkeypatch.setattr(cloud_init, "initialize_cloud_root_folder", fake_root)
    monkeypatch.setattr(cloud_init, "get_matter_provisioning_tokens", fake_tokens)
    monkeypatch.setattr(cloud_init, "initialize_matter_folders", fake_matter_init)

    response = await client.post("/api/integrations/cloud-init/retry")

    assert response.status_code == 200
    assert response.json() == {
        "root": {"onedrive": {"id": "root-id"}},
        "root_providers": ["onedrive"],
        "matters_checked": 2,
        "matters_initialized": 1,
        "matters_failed": 1,
        "status": "partial",
    }
    assert len(attempts) == 2
    assert {matter_id for matter_id, _tokens in attempts} == matter_ids
    assert all(
        tokens == {"microsoft": "fresh-token"} for _matter_id, tokens in attempts
    )


@pytest.mark.asyncio
async def test_matter_provisioning_reuses_tokens_without_refreshing_inside_savepoint(
    monkeypatch,
):
    """The retry route can keep savepoints free of token-refresh commits."""
    tokens = {"microsoft": "already-fresh"}
    monkeypatch.setattr(cloud_init, "get_fresh_token", pytest.fail)
    ensure = AsyncMock(return_value="folder-id")
    monkeypatch.setattr(cloud_init, "_ensure_onedrive_folder", ensure)
    monkeypatch.setattr(
        cloud_init,
        "_get_onedrive_folder_metadata",
        AsyncMock(return_value={"name": "Matter"}),
    )
    monkeypatch.setattr(cloud_init, "ensure_matter_marker", AsyncMock())

    matter_id = uuid.uuid4()
    result = await cloud_init.initialize_matter_folders(
        None,
        str(uuid.uuid4()),
        "matter",
        {"onedrive": {"id": "root-id"}},
        matter_id=matter_id,
        tokens=tokens,
    )

    assert result["onedrive"]["matter_folder_id"] == "folder-id"

# Integrations + Daily User Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relabel the admin "Permissions" panel to "Integrations", surface a synced-user count + last-sync freshness per provider, run a daily directory user sync, and ensure synced users land on the free tier.

**Architecture:** Persist last-sync results on `TenantCredential`. `UserSyncService` writes those fields and creates synced users unlicensed. A new `user-sync` APScheduler job runs nightly and is manually triggerable through the existing scheduler endpoint. `/admin/permissions` gains count + freshness fields; the renamed `IntegrationsPanel` displays them with a "Sync now" button.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, APScheduler, React (Vite), pytest-asyncio (real Postgres test DB).

---

## File Structure

- `backend/app/models/tenant_credential.py` — add 6 last-sync columns
- `backend/migrations/versions/030_user_sync_state.py` — new migration (create)
- `backend/app/services/user_sync.py` — persist sync state + license guardrail
- `backend/app/services/scheduler.py` — `user-sync` job + registry/map/count
- `backend/app/routers/scheduler.py` — add `user-sync` to the API registry (gates trigger)
- `backend/app/routers/admin.py` — `/admin/permissions` count + freshness fields
- `backend/tests/test_user_sync_state.py` — new test file (create)
- `backend/tests/test_user_sync_scheduler.py` — new test file (create)
- `frontend/src/pages/AdminPage.jsx` — tab rename + import
- `frontend/src/components/PermissionsAudit.jsx` → `IntegrationsPanel.jsx` — rename + UI
- `frontend/src/api.js` — `triggerUserSync()`

---

### Task 1: Add last-sync columns to TenantCredential + migration

**Files:**
- Modify: `backend/app/models/tenant_credential.py`
- Create: `backend/migrations/versions/030_user_sync_state.py`
- Test: `backend/tests/test_user_sync_state.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_user_sync_state.py`:

```python
import uuid

import pytest
from sqlalchemy import select

from app.models.tenant_credential import TenantCredential


@pytest.mark.asyncio
async def test_tenant_credential_has_sync_state_columns(db_session, test_tenant):
    cred = TenantCredential(
        tenant_id=test_tenant.id,
        provider="google",
        encrypted_access_token="enc",
        scopes="https://www.googleapis.com/auth/admin.directory.user.readonly",
        is_active=True,
        last_user_sync_total=3,
        last_user_sync_created=2,
        last_user_sync_updated=1,
        last_user_sync_status="ok",
    )
    db_session.add(cred)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "google",
            )
        )
    ).scalar_one()
    assert row.last_user_sync_total == 3
    assert row.last_user_sync_status == "ok"
    assert row.last_user_sync_at is None
    assert row.last_user_sync_error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py::test_tenant_credential_has_sync_state_columns -v`
Expected: FAIL with `TypeError: 'last_user_sync_total' is an invalid keyword argument for TenantCredential`

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/tenant_credential.py`, change the import line 3 to add `Integer`:

```python
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Integer
```

Then insert these columns immediately after the `scopes` column (after line 30, before `service_account_email`):

```python
    last_user_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_user_sync_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_user_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py::test_tenant_credential_has_sync_state_columns -v`
Expected: PASS

- [ ] **Step 5: Create the migration**

Create `backend/migrations/versions/030_user_sync_state.py`:

```python
"""030 — User sync state on tenant_credentials

Revision ID: 030
Revises: 029
Create Date: 2026-06-03

Adds last-sync bookkeeping columns to tenant_credentials so the Integrations
panel can show how many directory users were pulled and when the last sync ran.
"""

from alembic import op
import sqlalchemy as sa

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_created", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_updated", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_credentials", "last_user_sync_error")
    op.drop_column("tenant_credentials", "last_user_sync_status")
    op.drop_column("tenant_credentials", "last_user_sync_updated")
    op.drop_column("tenant_credentials", "last_user_sync_created")
    op.drop_column("tenant_credentials", "last_user_sync_total")
    op.drop_column("tenant_credentials", "last_user_sync_at")
```

- [ ] **Step 6: Verify the migration applies cleanly**

Run: `cd backend && py -m alembic upgrade head && py -m alembic downgrade -1 && py -m alembic upgrade head`
Expected: no errors; `030` becomes head.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/tenant_credential.py backend/migrations/versions/030_user_sync_state.py backend/tests/test_user_sync_state.py
git commit -m "feat: add user-sync state columns to tenant_credentials (030)"
```

---

### Task 2: Persist sync state + free-tier guardrail in UserSyncService

**Files:**
- Modify: `backend/app/services/user_sync.py`
- Test: `backend/tests/test_user_sync_state.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_user_sync_state.py`:

```python
from unittest.mock import AsyncMock, patch

from app.models.user import User
from app.services.user_sync import UserSyncService


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        return _FakeResp(200, self._payload)


@pytest.mark.asyncio
async def test_ms_sync_creates_free_tier_user_and_records_state(db_session, test_tenant):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="microsoft",
            encrypted_access_token="enc",
            scopes="User.Read.All",
            is_active=True,
        )
    )
    await db_session.commit()

    payload = {
        "value": [
            {"id": "ms-1", "mail": "new.hire@testfirm.com", "displayName": "New Hire"}
        ]
    }
    with patch(
        "app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")
    ), patch(
        "app.services.user_sync.httpx.AsyncClient",
        return_value=_FakeClient(payload),
    ):
        res = await UserSyncService().sync_microsoft_users(
            db_session, str(test_tenant.id)
        )

    assert res["created"] == 1
    user = (
        await db_session.execute(
            select(User).where(User.email == "new.hire@testfirm.com")
        )
    ).scalar_one()
    # Regression B: synced users land on free tier
    assert user.license_active is False

    cred = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "microsoft",
            )
        )
    ).scalar_one()
    assert cred.last_user_sync_status == "ok"
    assert cred.last_user_sync_total == 1
    assert cred.last_user_sync_at is not None


@pytest.mark.asyncio
async def test_sync_does_not_relicense_existing_user(db_session, test_tenant):
    # Existing licensed user (e.g. firm owner) already in the directory result
    db_session.add(
        User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email="owner@testfirm.com",
            full_name="Owner",
            role="admin",
            is_active=True,
            license_active=True,
        )
    )
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="microsoft",
            encrypted_access_token="enc",
            scopes="User.Read.All",
            is_active=True,
        )
    )
    await db_session.commit()

    payload = {
        "value": [
            {"id": "ms-9", "mail": "owner@testfirm.com", "displayName": "Owner"}
        ]
    }
    with patch(
        "app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")
    ), patch(
        "app.services.user_sync.httpx.AsyncClient",
        return_value=_FakeClient(payload),
    ):
        await UserSyncService().sync_microsoft_users(db_session, str(test_tenant.id))

    owner = (
        await db_session.execute(
            select(User).where(User.email == "owner@testfirm.com")
        )
    ).scalar_one()
    # Regression B: existing license untouched by sync
    assert owner.license_active is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py -v -k "free_tier or relicense"`
Expected: FAIL — `test_ms_sync_creates_free_tier_user_and_records_state` fails on `assert user.license_active is False` (currently defaults True) and/or `last_user_sync_status` is None.

- [ ] **Step 3: Add datetime import**

In `backend/app/services/user_sync.py`, add after line 1 (`import logging`):

```python
from datetime import datetime, timezone
```

- [ ] **Step 4: Add the persistence helpers**

In `backend/app/services/user_sync.py`, inside `class UserSyncService`, add these methods just below the class definition (before `sync_microsoft_users`):

```python
    async def _save_sync_state(
        self,
        db: AsyncSession,
        tenant_id: str,
        provider: str,
        *,
        status: str,
        total: int = 0,
        created: int = 0,
        updated: int = 0,
        error: str | None = None,
    ) -> None:
        from sqlalchemy import update

        from app.models.tenant_credential import TenantCredential

        await db.execute(
            update(TenantCredential)
            .where(
                TenantCredential.tenant_id == uuid.UUID(tenant_id),
                TenantCredential.provider == provider,
            )
            .values(
                last_user_sync_at=datetime.now(timezone.utc),
                last_user_sync_total=total,
                last_user_sync_created=created,
                last_user_sync_updated=updated,
                last_user_sync_status=status,
                last_user_sync_error=error,
            )
        )
        await db.commit()

    async def record_sync_failure(
        self, db: AsyncSession, tenant_id: str, provider: str, error: str
    ) -> None:
        await self._save_sync_state(
            db, tenant_id, provider, status="failed", error=error
        )
```

- [ ] **Step 5: Set new users to free tier (both providers)**

In `sync_microsoft_users`, in the `else:` branch that builds `new_user = User(...)` (around line 84), add `license_active=False,` to the constructor:

```python
                    new_user = User(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(tenant_id),
                        email=email,
                        full_name=full_name or email.split("@")[0],
                        role="user",
                        oauth_provider="microsoft",
                        oauth_subject=ms_user.get("id"),
                        is_active=True,
                        license_active=False,
                    )
```

In `sync_google_users`, in the matching `else:` branch (around line 175), add the same `license_active=False,` line to the `new_user = User(...)` constructor.

- [ ] **Step 6: Record success state in both sync methods**

In `sync_microsoft_users`, immediately after `await db.commit()` (line 97, still inside the `async with httpx.AsyncClient()` block) and before the `return {...}`:

```python
            await self._save_sync_state(
                db,
                tenant_id,
                "microsoft",
                status="ok",
                total=len(all_users),
                created=created,
                updated=updated,
            )
```

In `sync_google_users`, immediately after its `await db.commit()` (line 188) and before its `return {...}`:

```python
            await self._save_sync_state(
                db,
                tenant_id,
                "google",
                status="ok",
                total=len(all_users),
                created=created,
                updated=updated,
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py -v`
Expected: PASS (all three tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/user_sync.py backend/tests/test_user_sync_state.py
git commit -m "feat: persist user-sync state and create synced users on free tier"
```

---

### Task 3: Daily user-sync scheduler job + manual trigger registration

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/routers/scheduler.py`
- Test: `backend/tests/test_user_sync_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_user_sync_scheduler.py`:

```python
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.scheduler import SchedulerLog
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services.scheduler import LegalScheduler


@pytest.mark.asyncio
async def test_user_sync_isolates_tenant_failure(db_session, test_engine, test_tenant):
    # Second tenant whose sync will fail
    bad_tenant = Tenant(
        id=uuid.uuid4(),
        name="Bad Firm",
        domain="badfirm.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(bad_tenant)
    for t in (test_tenant, bad_tenant):
        db_session.add(
            TenantCredential(
                tenant_id=t.id,
                provider="microsoft",
                encrypted_access_token="enc",
                scopes="User.Read.All",
                is_active=True,
            )
        )
    await db_session.commit()

    async def fake_ms(db, tenant_id):
        if tenant_id == str(bad_tenant.id):
            raise RuntimeError("expired token")
        return {"created": 0, "updated": 1, "skipped": 0, "total": 1}

    test_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    with patch("app.services.scheduler.async_session_maker", test_maker), patch(
        "app.services.user_sync.user_sync.sync_microsoft_users",
        new=AsyncMock(side_effect=fake_ms),
    ):
        await LegalScheduler().run_user_sync()

    # Run completed despite one tenant failing
    log = (
        await db_session.execute(
            select(SchedulerLog)
            .where(SchedulerLog.agent_name == "user-sync")
            .order_by(SchedulerLog.run_at.desc())
        )
    ).scalars().first()
    assert log is not None
    assert log.status == "completed"

    # Failing tenant's credential recorded a failure
    bad_cred = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == bad_tenant.id,
                TenantCredential.provider == "microsoft",
            )
        )
    ).scalar_one()
    assert bad_cred.last_user_sync_status == "failed"
    assert "expired token" in (bad_cred.last_user_sync_error or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_user_sync_scheduler.py -v`
Expected: FAIL with `AttributeError: 'LegalScheduler' object has no attribute 'run_user_sync'`

- [ ] **Step 3: Add the `run_user_sync` method**

In `backend/app/services/scheduler.py`, add this method to `class LegalScheduler` immediately after `run_cloud_sync` (after line 989):

```python
    async def run_user_sync(self) -> None:
        """Sync directory users for every tenant with active credentials.

        Per-tenant/per-provider failures are isolated so one bad token cannot
        abort the whole run. Synced users land on the free tier (license_active
        is left to the UserSyncService default of False for new users).
        """
        logger.info("[user-sync] Starting run")
        from app.services.user_sync import user_sync as user_sync_svc

        async with async_session_maker() as session:
            log = await _log_start(session, "user-sync")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(TenantCredential.tenant_id, TenantCredential.provider).where(
                        TenantCredential.is_active
                    )
                )
                pairs = result.all()

                if not pairs:
                    await _log_complete(
                        session, log, "No active credentials — nothing to sync."
                    )
                    logger.info("[user-sync] No connected tenants.")
                    return

                synced = 0
                failed = 0
                total_users = 0

                for tenant_id, provider in pairs:
                    try:
                        if provider == "microsoft":
                            res = await user_sync_svc.sync_microsoft_users(
                                session, str(tenant_id)
                            )
                        elif provider == "google":
                            res = await user_sync_svc.sync_google_users(
                                session, str(tenant_id)
                            )
                        else:
                            continue
                        synced += 1
                        total_users += res.get("total", 0)
                    except Exception as tenant_err:
                        failed += 1
                        logger.warning(
                            "[user-sync] %s sync failed for tenant %s: %s",
                            provider,
                            tenant_id,
                            tenant_err,
                        )
                        await _bypass_rls(session)
                        try:
                            await user_sync_svc.record_sync_failure(
                                session, str(tenant_id), provider, str(tenant_err)
                            )
                        except Exception as record_err:
                            logger.warning(
                                "[user-sync] Could not record failure for tenant %s: %s",
                                tenant_id,
                                record_err,
                            )

                await _bypass_rls(session)
                summary = (
                    f"Synced {synced} credential(s); {total_users} user(s) seen; "
                    f"{failed} failed."
                )
                await _log_complete(session, log, summary)
                logger.info("[user-sync] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[user-sync] Unhandled error: %s", error_msg)
                await _bypass_rls(session)
                await _log_failed(session, log, error_msg)
```

- [ ] **Step 4: Register the job, registry entry, manual-trigger map, and count**

In `backend/app/services/scheduler.py`:

(a) Add to `AGENT_REGISTRY` (after the `cloud-sync` entry, around line 77):

```python
    {
        "name": "user-sync",
        "display_name": "Directory User Sync",
        "description": "Pulls directory users from connected Google/Microsoft tenants nightly; new users land on the free tier.",
        "schedule": "Daily at 2:00 AM ET",
    },
```

(b) In `start()`, after the `task-reminder` job block (after line 336) and before `agent_count = 5`, add the job and bump the base count to 6:

```python
        # user-sync: daily at 2:00 AM ET
        self.scheduler.add_job(
            self.run_user_sync,
            CronTrigger(hour=2, minute=0),
            id="user-sync",
            name="Directory User Sync",
            replace_existing=True,
        )

        agent_count = 6
```

(Replace the existing `agent_count = 5` line with `agent_count = 6` per above.)

(c) In `run_agent_manually`, add to `agent_map` (after the `cloud-sync` entry, around line 1004):

```python
            "user-sync": self.run_user_sync,
```

- [ ] **Step 5: Add `user-sync` to the router registry (gates the trigger endpoint)**

In `backend/app/routers/scheduler.py`, add to its `AGENT_REGISTRY` list (after the `oc-status` entry, around line 44). This is REQUIRED — `trigger_agent` validates against this list, so "Sync now" 404s without it:

```python
    {
        "name": "user-sync",
        "display_name": "Directory User Sync",
        "description": "Pulls directory users from connected Google/Microsoft tenants; new users land on the free tier.",
        "schedule": "Daily at 2:00 AM ET",
    },
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_user_sync_scheduler.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/routers/scheduler.py backend/tests/test_user_sync_scheduler.py
git commit -m "feat: add nightly user-sync scheduler job with manual trigger"
```

---

### Task 4: Expose user count + freshness on /admin/permissions

**Files:**
- Modify: `backend/app/routers/admin.py:1124-1181`
- Test: `backend/tests/test_user_sync_state.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_user_sync_state.py`:

```python
@pytest.mark.asyncio
async def test_permissions_returns_user_count_and_freshness(
    client, db_session, test_tenant, test_user
):
    # test_user has oauth_provider="google"; add a connected google credential
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token="enc",
            scopes="https://www.googleapis.com/auth/admin.directory.user.readonly",
            is_active=True,
            last_user_sync_total=5,
            last_user_sync_status="ok",
        )
    )
    await db_session.commit()

    resp = await client.get("/api/admin/permissions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["google"]["user_count"] >= 1
    assert data["google"]["last_sync_total"] == 5
    assert data["google"]["last_sync_status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py::test_permissions_returns_user_count_and_freshness -v`
Expected: FAIL with `KeyError: 'user_count'`

- [ ] **Step 3: Confirm imports**

In `backend/app/routers/admin.py`, ensure `func` and `User` are imported. Add whichever is missing near the existing imports:

```python
from sqlalchemy import select, func
from app.models.user import User
```

- [ ] **Step 4: Compute per-provider counts and extend the audit**

In `get_permissions_audit` (`admin.py:1124`), after `creds = cred_result.scalars().all()` (line 1137), add live counts:

```python
    async def _provider_user_count(provider: str) -> int:
        return (
            await db.scalar(
                select(func.count(User.id)).where(
                    User.tenant_id == tenant_id,
                    User.oauth_provider == provider,
                )
            )
            or 0
        )

    ms_count = await _provider_user_count("microsoft")
    google_count = await _provider_user_count("google")
```

Then change `audit_provider` to accept and emit the new fields. Replace the existing `def audit_provider(...)` body (lines 1139-1160) with:

```python
    def audit_provider(provider: str, required: list[str], user_count: int) -> dict:
        match = next((c for c in creds if c.provider == provider), None)
        freshness = {
            "user_count": user_count,
            "last_sync_at": match.last_user_sync_at.isoformat()
            if match and match.last_user_sync_at
            else None,
            "last_sync_total": match.last_user_sync_total if match else None,
            "last_sync_status": match.last_user_sync_status if match else None,
        }
        if not match or not match.scopes:
            return {
                "connected": False,
                "granted_scopes": [],
                "missing_required": required,
                "extra_scopes": [],
                "all_required": False,
                "health": "disconnected",
                **freshness,
            }
        granted = [s.strip() for s in match.scopes.split(" ") if s.strip()]
        missing = [s for s in required if s not in granted]
        extra = [s for s in granted if s not in required]
        return {
            "connected": True,
            "granted_scopes": granted,
            "missing_required": missing,
            "extra_scopes": extra,
            "all_required": len(missing) == 0,
            "health": "healthy" if len(missing) == 0 else "missing_scopes",
            **freshness,
        }
```

Update the two call sites (lines 1162-1163):

```python
    ms_audit = audit_provider("microsoft", SCOPES_REQUIRED_MS, ms_count)
    google_audit = audit_provider("google", SCOPES_REQUIRED_GOOGLE, google_count)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py::test_permissions_returns_user_count_and_freshness -v`
Expected: PASS

- [ ] **Step 6: Run the full backend suite for regressions**

Run: `cd backend && py -m pytest tests/test_user_sync_state.py tests/test_user_sync_scheduler.py tests/test_cloud_integrations.py tests/test_onboarding.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/admin.py backend/tests/test_user_sync_state.py
git commit -m "feat: expose synced user count and last-sync freshness on /admin/permissions"
```

---

### Task 5: Frontend — rename to Integrations, show count/freshness, Sync now

**Files:**
- Modify: `frontend/src/pages/AdminPage.jsx`
- Rename + modify: `frontend/src/components/PermissionsAudit.jsx` → `frontend/src/components/IntegrationsPanel.jsx`
- Modify: `frontend/src/api.js`

> Note: this project has no JS test harness; verify via build + click-through (Step 6).

- [ ] **Step 1: Add the API helper**

In `frontend/src/api.js`, after `getAdminPermissions` (line 187), add:

```javascript
export const triggerUserSync = () =>
  api.post('/scheduler/agents/user-sync/run').then((r) => r.data)
```

- [ ] **Step 2: Rename the component file**

Run:

```bash
git mv frontend/src/components/PermissionsAudit.jsx frontend/src/components/IntegrationsPanel.jsx
```

- [ ] **Step 3: Rename the component + add count/freshness/Sync now**

In `frontend/src/components/IntegrationsPanel.jsx`:

(a) Update imports (line 1-2):

```javascript
import React, { useEffect, useState } from 'react'
import { getAdminPermissions, triggerUserSync } from '../api'
```

(b) Rename the default export (line 20) from `PermissionsAudit` to `IntegrationsPanel`:

```javascript
export default function IntegrationsPanel() {
```

(c) Add a relative-time helper and sync handler near the top of `IntegrationsPanel`, right after the `data`/`loading`/`error` state declarations:

```javascript
  const [syncing, setSyncing] = useState(false)

  const relTime = (iso) => {
    if (!iso) return 'never'
    const diffMs = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diffMs / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  }

  const handleSyncNow = async () => {
    setSyncing(true)
    try {
      await triggerUserSync()
      // Sync runs async on the server; refresh after a short delay.
      setTimeout(() => {
        getAdminPermissions().then(setData).catch(() => {})
        setSyncing(false)
      }, 4000)
    } catch {
      setError('Failed to trigger sync.')
      setSyncing(false)
    }
  }
```

(d) Inside `ProviderCard`, just below the status-pill `<div>` block (after the closing `</div>` of the name/pill group, before the "Re-authorize" button), add a freshness line. Add these props to `ProviderCard`'s signature first — change it to:

```javascript
function ProviderCard({ name, provider, info, scopeLabels, onReauthorize, relTime, onSyncNow, syncing }) {
```

Then inside the header `<div className="flex items-center justify-between mb-4">`, replace the single Re-authorize button with a button group:

```javascript
        <div className="flex items-center gap-2">
          {info.connected && (
            <button
              onClick={onSyncNow}
              disabled={syncing}
              className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
            >
              {syncing ? 'Syncing…' : 'Sync now'}
            </button>
          )}
          <button
            onClick={() => onReauthorize(provider)}
            className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors"
          >
            {info.connected ? 'Re-authorize' : 'Connect'}
          </button>
        </div>
```

And add a freshness line directly under the `<span>` status pill (inside the left `<div>` that holds the `<h3>` and pill):

```javascript
          {info.connected && (
            <p className="mt-1 text-xs text-brand-ink-2 font-sans">
              {info.user_count ?? 0} users synced
              {info.last_sync_status === 'failed' ? ' · last sync failed' : ` · last run ${relTime(info.last_sync_at)}`}
            </p>
          )}
```

(e) Pass the new props at both `<ProviderCard ... />` call sites (Microsoft and Google):

```javascript
        relTime={relTime}
        onSyncNow={handleSyncNow}
        syncing={syncing}
```

(f) Update the header text. Change the overall-status label and any "Permissions" heading text to "Integrations" where it appears (the overall status badge prefix `Overall:` can stay).

- [ ] **Step 4: Update AdminPage tab + import**

In `frontend/src/pages/AdminPage.jsx`:

(a) Line 9 import:

```javascript
import IntegrationsPanel from '../components/IntegrationsPanel'
```

(b) Line 434 tab entry — change:

```javascript
    { id: 'integrations', label: 'Integrations' },
```

(c) Line 517 render guard — change:

```javascript
          {activeTab === 'integrations' && <IntegrationsPanel />}
```

- [ ] **Step 5: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds with no unresolved-import errors.

- [ ] **Step 6: Manual verification (via deployed tunnel)**

After deploy, sign in as admin → Admin Panel → confirm the tab now reads **Integrations**, each connected provider shows "N users synced · last run …", and "Sync now" triggers a run (check `/api/scheduler/logs` for a `user-sync` entry, then the count/freshness refreshes).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/AdminPage.jsx frontend/src/components/IntegrationsPanel.jsx frontend/src/api.js
git commit -m "feat: relabel admin Permissions tab to Integrations with sync count and Sync now"
```

---

### Task 6: Update CHANGELOG and TASKS

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Add a CHANGELOG entry**

Under the current version's `### Added` / `### Changed` / `### Fixed` sections, add:

- Added: Daily directory user sync (`user-sync` scheduler job, 2:00 AM ET) + "Sync now" in the admin Integrations panel.
- Added: Synced-user count and last-sync freshness on `/admin/permissions`; `tenant_credentials` last-sync columns (migration 030).
- Changed: Admin "Permissions" tab renamed to "Integrations".
- Fixed: Directory-synced users now land on the free tier (`license_active=False`) instead of auto-consuming a license seat.

- [ ] **Step 2: Update TASKS.md**

Add a completed task entry under Sprint 8 describing the Integrations hub + daily user sync, with checkboxes checked.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md TASKS.md
git commit -m "docs: changelog and tasks for integrations hub + daily user sync"
```

---

## Self-Review

**Spec coverage:**
- UI relabel → Task 5 ✓
- Persist last-sync state (migration 030) → Tasks 1, 2 ✓
- Daily sync + manual trigger → Task 3 ✓
- Licensing guardrail (free tier) → Task 2 (Step 5) + regression tests ✓
- API count + freshness → Task 4 ✓
- Error handling (per-tenant isolation) → Task 3 + test ✓
- Tests (regression A token failure; regression B license) → Tasks 2, 3 ✓

**Type/name consistency:** `_save_sync_state` / `record_sync_failure` defined in Task 2, called in Task 3; `run_user_sync` defined and registered in Task 3; `user_count`/`last_sync_*` emitted in Task 4 and consumed in Task 5; `triggerUserSync` defined in Task 5 Step 1, used in Step 3. Column names match across model (Task 1), migration (Task 1), persistence (Task 2), and API (Task 4).

**Placeholder scan:** none — every step shows concrete code/commands.

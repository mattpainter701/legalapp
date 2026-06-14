# Provider Tier Detection & Capability Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect whether a connected Google/Microsoft account is a Workspace/Azure-AD tenant or a personal account, resolve which features each tier supports, and stop reporting tier-incompatible features (directory sync on personal Gmail) as hard failures.

**Architecture:** Capture the account tier from the id_token claims already decoded in each OAuth callback (Google `hd`, Microsoft `tid`); store it on `tenant_credentials`. A pure capability resolver maps (provider, tier, scopes, last_sync_status) → a per-feature matrix. User sync consults the resolver and records `not_applicable` (not `failed`) when a feature doesn't apply to the tier. The admin permissions API and `IntegrationsPanel` surface the tier badge + matrix.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, pytest/pytest-asyncio, React (Vite).

**Spec:** `docs/superpowers/specs/2026-06-14-provider-tier-detection-capabilities-design.md`

**Conventions:** Run backend tests with `py -m pytest` from `backend/`. Tests build their schema via `Base.metadata.create_all`, so new model columns are available to tests without running Alembic; the migration is only needed for production. The live admin UI reads `GET /api/admin/permissions` (function `get_permissions_audit` in `app/routers/admin.py`), rendered by `ProviderCard` in `frontend/src/components/IntegrationsPanel.jsx`.

---

## File Structure

- Create: `backend/migrations/versions/056_account_tier.py` — adds 3 nullable columns to `tenant_credentials`.
- Modify: `backend/app/models/tenant_credential.py` — add `account_type`, `account_domain`, `account_detected_at`.
- Create: `backend/app/services/capabilities.py` — pure capability resolver + tier constants + `account_label`.
- Create: `backend/app/services/account_detect.py` — claim-based detection, live backfill, `apply_detection`/`persist`.
- Modify: `backend/app/services/user_sync.py` — short-circuit tier-incompatible directory sync to `not_applicable`.
- Modify: `backend/app/routers/integrations.py` — wire detection into both OAuth callbacks; add fields to `/status`.
- Modify: `backend/app/schemas/integrations.py` — add account/capability fields to `IntegrationStatus`.
- Modify: `backend/app/routers/admin.py` — `get_permissions_audit` returns tier + capabilities; best-effort backfill.
- Modify: `frontend/src/components/IntegrationsPanel.jsx` — tier badge, capability matrix, suppress failed banner for `not_applicable`.
- Create: `backend/tests/test_account_detect.py`, `backend/tests/test_capabilities.py` — unit tables.
- Modify: `backend/tests/test_user_sync_state.py` — honest-sync regression.

---

## Task 1: Model columns + migration

**Files:**
- Modify: `backend/app/models/tenant_credential.py`
- Create: `backend/migrations/versions/056_account_tier.py`
- Test: `backend/tests/test_account_detect.py` (new file, first test)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_account_detect.py`:

```python
import pytest
from sqlalchemy import select

from app.models.tenant_credential import TenantCredential


@pytest.mark.asyncio
async def test_tenant_credential_has_account_tier_columns(db_session, test_tenant):
    from datetime import datetime, timezone

    cred = TenantCredential(
        tenant_id=test_tenant.id,
        provider="google",
        encrypted_access_token="enc",
        is_active=True,
        account_type="workspace",
        account_domain="myfirm.com",
        account_detected_at=datetime.now(timezone.utc),
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
    assert row.account_type == "workspace"
    assert row.account_domain == "myfirm.com"
    assert row.account_detected_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_account_detect.py::test_tenant_credential_has_account_tier_columns -v`
Expected: FAIL — `TypeError: 'account_type' is an invalid keyword argument` (column not defined).

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/tenant_credential.py`, after the `service_account_email` column (line ~39-41), add:

```python
    account_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_account_detect.py::test_tenant_credential_has_account_tier_columns -v`
Expected: PASS.

- [ ] **Step 5: Create the migration**

Create `backend/migrations/versions/056_account_tier.py`:

```python
"""056 — Account tier detection columns on tenant_credentials.

Adds nullable columns recording whether a connected Google/Microsoft account is
a Workspace/Azure-AD tenant or a personal account, the detected domain, and when
detection last ran. All nullable; existing rows classify lazily as ``unknown``.
"""

from alembic import op
import sqlalchemy as sa

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_credentials",
        sa.Column("account_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("account_domain", sa.String(255), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("account_detected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_credentials", "account_detected_at")
    op.drop_column("tenant_credentials", "account_domain")
    op.drop_column("tenant_credentials", "account_type")
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/tenant_credential.py backend/migrations/versions/056_account_tier.py backend/tests/test_account_detect.py
git commit -m "feat: account tier columns on tenant_credentials (migration 056)"
```

---

## Task 2: Capability resolver (pure)

**Files:**
- Create: `backend/app/services/capabilities.py`
- Test: `backend/tests/test_capabilities.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_capabilities.py`:

```python
from app.services import capabilities as cap

GOOGLE_DIR = "https://www.googleapis.com/auth/admin.directory.user.readonly"


def test_personal_google_directory_sync_unavailable():
    m = cap.resolve("google", "personal", "https://www.googleapis.com/auth/drive", None)
    assert m["directory_sync"]["available"] is False
    assert m["directory_sync"]["status"] == "unavailable"
    assert "personal" in m["directory_sync"]["reason"].lower()
    # storage/email/calendar still work on personal Gmail
    assert m["cloud_storage"]["available"] is True
    assert m["calendar"]["available"] is True


def test_workspace_directory_sync_ok_with_scope():
    m = cap.resolve("google", "workspace", GOOGLE_DIR, "ok")
    assert m["directory_sync"]["available"] is True
    assert m["directory_sync"]["status"] == "ok"


def test_workspace_directory_sync_needs_reauth_without_scope():
    m = cap.resolve("google", "workspace", "https://www.googleapis.com/auth/drive", None)
    assert m["directory_sync"]["available"] is True
    assert m["directory_sync"]["status"] == "needs_reauth"


def test_workspace_directory_sync_error_when_last_failed():
    m = cap.resolve("google", "workspace", GOOGLE_DIR, "failed")
    assert m["directory_sync"]["status"] == "error"


def test_unknown_tier_directory_sync_unavailable():
    m = cap.resolve("google", "unknown", GOOGLE_DIR, None)
    assert m["directory_sync"]["available"] is False
    assert "reconnect" in m["directory_sync"]["reason"].lower()


def test_consumer_microsoft_directory_and_teams_unavailable():
    m = cap.resolve("microsoft", "consumer", "User.Read", None)
    assert m["directory_sync"]["available"] is False
    assert m["teams"]["available"] is False


def test_azure_ad_directory_ok_and_teams_needs_reauth_without_scope():
    m = cap.resolve("microsoft", "azure_ad", "User.Read.All", "ok")
    assert m["directory_sync"]["status"] == "ok"
    assert m["teams"]["status"] == "needs_reauth"


def test_google_has_no_teams_feature():
    m = cap.resolve("google", "workspace", GOOGLE_DIR, "ok")
    assert "teams" not in m


def test_account_label():
    assert cap.account_label("google", "personal") == "Personal Google (Gmail)"
    assert cap.account_label("google", "workspace") == "Google Workspace"
    assert cap.account_label("microsoft", "consumer") == "Personal Microsoft Account"
    assert cap.account_label("microsoft", "azure_ad") == "Microsoft 365 (Work/School)"
    assert cap.account_label("google", None) == "Google (tier unknown)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -m pytest tests/test_capabilities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.capabilities'`.

- [ ] **Step 3: Implement the resolver**

Create `backend/app/services/capabilities.py`:

```python
"""Pure capability resolution for connected cloud providers.

Maps (provider, account_type, granted scopes, last directory-sync status) to a
per-feature availability matrix. No I/O — trivially unit-testable.
"""

from app.services.teams_gate import missing_teams_scopes

# account_type values
WORKSPACE = "workspace"
PERSONAL = "personal"
AZURE_AD = "azure_ad"
CONSUMER = "consumer"
UNKNOWN = "unknown"

GOOGLE_DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
MS_DIRECTORY_SCOPE = "User.Read.All"

_DIRECTORY_TIERS = {WORKSPACE, AZURE_AD}

_LABELS = {
    ("google", WORKSPACE): "Google Workspace",
    ("google", PERSONAL): "Personal Google (Gmail)",
    ("microsoft", AZURE_AD): "Microsoft 365 (Work/School)",
    ("microsoft", CONSUMER): "Personal Microsoft Account",
}


def account_label(provider: str, account_type: str | None) -> str:
    if account_type and (provider, account_type) in _LABELS:
        return _LABELS[(provider, account_type)]
    pretty = "Google" if provider == "google" else "Microsoft"
    return f"{pretty} (tier unknown)"


def _cap(available: bool, status: str, reason: str) -> dict:
    return {"available": available, "status": status, "reason": reason}


def _directory_sync(provider: str, account_type: str | None,
                    scopes_set: set[str], last_sync_status: str | None) -> dict:
    if account_type in (None, UNKNOWN):
        return _cap(False, "unavailable",
                    "Account tier not yet detected — reconnect to confirm directory access.")
    if account_type not in _DIRECTORY_TIERS:
        # personal / consumer
        if provider == "google":
            reason = ("Directory sync isn't available on personal Google accounts (Gmail). "
                      "Your Drive, Gmail, and Calendar still work.")
        else:
            reason = ("Directory sync isn't available on personal Microsoft accounts. "
                      "Your OneDrive, mail, and Calendar still work.")
        return _cap(False, "unavailable", reason)
    required = GOOGLE_DIRECTORY_SCOPE if provider == "google" else MS_DIRECTORY_SCOPE
    if required not in scopes_set:
        return _cap(True, "needs_reauth",
                    "Reconnect and grant directory read access to enable user sync.")
    if last_sync_status == "failed":
        return _cap(True, "error",
                    "The last directory sync failed — see the error detail and reconnect if needed.")
    return _cap(True, "ok", "Directory sync is available.")


def _teams(account_type: str | None, scopes: str | None) -> dict:
    if account_type in (None, UNKNOWN):
        return _cap(False, "unavailable",
                    "Account tier not yet detected — reconnect to confirm Teams access.")
    if account_type != AZURE_AD:
        return _cap(False, "unavailable",
                    "Microsoft Teams requires a Microsoft 365 Work/School account.")
    if missing_teams_scopes(scopes):
        return _cap(True, "needs_reauth",
                    "Reconnect with Teams enabled to grant the required Teams scopes.")
    return _cap(True, "ok", "Teams integration is available.")


def resolve(provider: str, account_type: str | None,
            scopes: str | None, last_sync_status: str | None) -> dict:
    scopes_set = {s for s in (scopes or "").split() if s}
    matrix = {
        "directory_sync": _directory_sync(provider, account_type, scopes_set, last_sync_status),
        "cloud_storage": _cap(True, "ok", "Cloud file storage is available."),
        "email": _cap(True, "ok", "Email access is available."),
        "calendar": _cap(True, "ok", "Calendar access is available."),
    }
    if provider == "microsoft":
        matrix["teams"] = _teams(account_type, scopes)
    return matrix
```

- [ ] **Step 4: Run to verify it passes**

Run: `py -m pytest tests/test_capabilities.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/capabilities.py backend/tests/test_capabilities.py
git commit -m "feat: pure capability resolver for provider tiers"
```

---

## Task 3: Detection service (claims + backfill)

**Files:**
- Create: `backend/app/services/account_detect.py`
- Test: `backend/tests/test_account_detect.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_account_detect.py`:

```python
from unittest.mock import AsyncMock, patch

from app.services import account_detect as det


def test_detect_google_workspace_from_hd():
    assert det.detect_google({"email": "a@firm.com", "hd": "firm.com"}) == ("workspace", "firm.com")


def test_detect_google_personal_without_hd():
    assert det.detect_google({"email": "a@gmail.com"}) == ("personal", None)


def test_detect_google_unknown_when_no_identity():
    assert det.detect_google({}) == ("unknown", None)
    assert det.detect_google(None) == ("unknown", None)


def test_detect_microsoft_consumer_from_tid():
    assert det.detect_microsoft(
        {"oid": "x", "tid": "9188040d-6c67-4c5b-b112-36a304b66dad"}
    ) == ("consumer", None)


def test_detect_microsoft_azure_ad_from_tid():
    t, dom = det.detect_microsoft({"oid": "x", "tid": "11111111-2222-3333-4444-555555555555"})
    assert t == "azure_ad"


def test_detect_microsoft_unknown_when_no_tid():
    assert det.detect_microsoft({}) == ("unknown", None)


@pytest.mark.asyncio
async def test_backfill_google_reads_hd_from_userinfo():
    class _Resp:
        status_code = 200
        def json(self):
            return {"email": "a@firm.com", "hd": "firm.com"}

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            return _Resp()

    with patch("app.services.account_detect.httpx.AsyncClient", return_value=_Client()):
        assert await det.backfill_google("tok") == ("workspace", "firm.com")
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -m pytest tests/test_account_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.account_detect'`.

- [ ] **Step 3: Implement the detection service**

Create `backend/app/services/account_detect.py`:

```python
"""Account tier detection from OAuth id_token claims (and live backfill)."""

from datetime import datetime, timezone
import logging

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import capabilities as cap

logger = logging.getLogger(__name__)

MS_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"
GRAPH_ORG = "https://graph.microsoft.com/v1.0/organization"


def detect_google(claims: dict | None) -> tuple[str, str | None]:
    if not isinstance(claims, dict) or not (claims.get("email") or claims.get("sub")):
        return (cap.UNKNOWN, None)
    hd = claims.get("hd")
    if hd:
        return (cap.WORKSPACE, str(hd))
    return (cap.PERSONAL, None)


def detect_microsoft(claims: dict | None) -> tuple[str, str | None]:
    if not isinstance(claims, dict):
        return (cap.UNKNOWN, None)
    tid = claims.get("tid")
    iss = claims.get("iss", "") or ""
    if tid == MS_CONSUMER_TENANT_ID or "/consumers/" in iss:
        return (cap.CONSUMER, None)
    if tid:
        return (cap.AZURE_AD, None)
    if claims.get("oid") or claims.get("sub"):
        return (cap.UNKNOWN, None)
    return (cap.UNKNOWN, None)


def apply_detection(cred, account_type: str, account_domain: str | None) -> None:
    """Set tier columns on a loaded TenantCredential (caller commits)."""
    cred.account_type = account_type
    cred.account_domain = account_domain
    cred.account_detected_at = datetime.now(timezone.utc)


async def persist(db: AsyncSession, tenant_id: str, provider: str,
                  account_type: str, account_domain: str | None) -> None:
    from app.models.tenant_credential import TenantCredential
    import uuid

    await db.execute(
        update(TenantCredential)
        .where(
            TenantCredential.tenant_id == uuid.UUID(str(tenant_id)),
            TenantCredential.provider == provider,
        )
        .values(
            account_type=account_type,
            account_domain=account_domain,
            account_detected_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


async def backfill_google(token: str) -> tuple[str, str | None]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(GOOGLE_USERINFO,
                                headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            return (cap.UNKNOWN, None)
        return detect_google(resp.json())


async def backfill_microsoft(token: str) -> tuple[str, str | None]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(GRAPH_ORG,
                                headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            return (cap.UNKNOWN, None)
        orgs = (resp.json() or {}).get("value") or []
        if not orgs:
            return (cap.CONSUMER, None)
        domains = orgs[0].get("verifiedDomains") or []
        primary = next((d.get("name") for d in domains if d.get("isDefault")), None)
        return (cap.AZURE_AD, primary)
```

- [ ] **Step 4: Run to verify it passes**

Run: `py -m pytest tests/test_account_detect.py -v`
Expected: PASS (all detection + backfill tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/account_detect.py backend/tests/test_account_detect.py
git commit -m "feat: account tier detection from id_token claims + live backfill"
```

---

## Task 4: Wire detection into OAuth callbacks

**Files:**
- Modify: `backend/app/routers/integrations.py` (google_callback ~line 423-455; microsoft_callback ~line 250-282)

This is a mechanical wiring change verified by the existing OAuth tests still passing plus an import check. No new unit test (full OAuth callback requires mocking token exchange end-to-end, out of proportion; detection logic is covered in Task 3).

- [ ] **Step 1: Add the import**

At the top of `backend/app/routers/integrations.py` with the other `from app.services...` imports:

```python
from app.services import account_detect
```

- [ ] **Step 2: Wire Microsoft callback**

In `microsoft_callback`, admin branch, after `claims = _json.loads(...)` populates `service_email` (the `try`/`except` block ~line 238-248), and after the `existing`/new-row write but **before** `await db.commit()` (~line 316), add detection. The decoded claims dict is `claims` — hoist it so it's accessible. Change the claims block to assign a module-scoped local:

```python
            service_email = None
            ms_claims = None
            id_token_raw = token_data.get("id_token")
            if id_token_raw:
                try:
                    payload_b64 = id_token_raw.split(".")[1]
                    payload_b64 += "=" * (4 - len(payload_b64) % 4)
                    ms_claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
                    service_email = (
                        ms_claims.get("email")
                        or ms_claims.get("preferred_username")
                        or ms_claims.get("upn")
                    )
                except Exception:
                    pass
```

Then, immediately after the `if existing: ... else: db.add(...)` block (still inside `if intent == "admin":`, before `await db.commit()`), add:

```python
            acct_type, acct_domain = account_detect.detect_microsoft(ms_claims)
            target = existing if existing else None
            if target is None:
                # re-fetch the row we just added so we can stamp it
                target = (
                    await db.execute(
                        select(TenantCredential).where(
                            TenantCredential.tenant_id == tenant_id,
                            TenantCredential.provider == "microsoft",
                        )
                    )
                ).scalar_one_or_none()
            if target is not None:
                account_detect.apply_detection(target, acct_type, acct_domain)
```

- [ ] **Step 3: Wire Google callback**

In `google_callback`, admin branch, the decoded claims var is `decoded` (~line 418). After the `if row: ... else: db.add(...)` block and before `await db.commit()` (~line 489), add:

```python
            acct_type, acct_domain = account_detect.detect_google(decoded if id_token else None)
            target = row
            if target is None:
                target = (
                    await db.execute(
                        select(TenantCredential).where(
                            TenantCredential.tenant_id == tenant_id,
                            TenantCredential.provider == "google",
                        )
                    )
                ).scalar_one_or_none()
            if target is not None:
                account_detect.apply_detection(target, acct_type, acct_domain)
```

Note: `decoded` is defined only inside the `if id_token:` block; initialize `decoded = None` just before that block so it always exists.

- [ ] **Step 4: Verify imports + existing tests pass**

Run: `py -c "import app.routers.integrations"`
Expected: no error.
Run: `py -m pytest tests/test_cloud_integrations.py tests/test_user_sync_state.py -q`
Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/integrations.py
git commit -m "feat: detect account tier on Google/Microsoft OAuth connect"
```

---

## Task 5: Honest sync — record `not_applicable` for incompatible tiers

**Files:**
- Modify: `backend/app/services/user_sync.py` (`sync_google_users` ~line 166, `sync_microsoft_users` ~line 66)
- Test: `backend/tests/test_user_sync_state.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_user_sync_state.py`:

```python
@pytest.mark.asyncio
async def test_personal_google_sync_records_not_applicable_not_failed(
    db_session, test_tenant
):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token="enc",
            scopes="https://www.googleapis.com/auth/drive",
            is_active=True,
            account_type="personal",
        )
    )
    await db_session.commit()

    called = {"http": False}

    class _Boom:
        async def __aenter__(self):
            called["http"] = True
            return self
        async def __aexit__(self, *a):
            return False

    with (
        patch("app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")),
        patch("app.services.user_sync.httpx.AsyncClient", return_value=_Boom()),
    ):
        res = await UserSyncService().sync_google_users(db_session, str(test_tenant.id))

    assert res["status"] == "not_applicable"
    assert called["http"] is False  # never hit the Directory API

    cred = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "google",
            )
        )
    ).scalar_one()
    assert cred.last_user_sync_status == "not_applicable"
    assert cred.last_user_sync_error and "personal" in cred.last_user_sync_error.lower()


@pytest.mark.asyncio
async def test_sync_all_does_not_flag_not_applicable_as_failure(db_session, test_tenant):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token="enc",
            scopes="https://www.googleapis.com/auth/drive",
            is_active=True,
            account_type="personal",
        )
    )
    await db_session.commit()

    with (
        patch("app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")),
    ):
        # microsoft has no credential → its own RuntimeError path; we only assert google
        res = await UserSyncService().sync_all(db_session, str(test_tenant.id))

    assert res["google"]["status"] == "not_applicable"
    assert "error" not in res["google"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -m pytest tests/test_user_sync_state.py::test_personal_google_sync_records_not_applicable_not_failed -v`
Expected: FAIL — the current code hits httpx / raises, status not recorded as `not_applicable`.

- [ ] **Step 3: Add a tier guard helper and call it in both sync methods**

In `backend/app/services/user_sync.py`, add imports near the top:

```python
from sqlalchemy import select, update
from app.services import capabilities as cap
```

(`select` is already imported; ensure `cap` import is added.)

Add a helper method on `UserSyncService`:

```python
    async def _directory_sync_blocked(
        self, db: AsyncSession, tenant_id: str, provider: str
    ) -> dict | None:
        """If this tenant's tier can't do directory sync, record not_applicable
        and return a result dict; otherwise return None to proceed."""
        from app.models.tenant_credential import TenantCredential

        row = (
            await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == uuid.UUID(str(tenant_id)),
                    TenantCredential.provider == provider,
                )
            )
        ).scalar_one_or_none()
        account_type = row.account_type if row else None
        scopes = row.scopes if row else None
        last = row.last_user_sync_status if row else None
        matrix = cap.resolve(provider, account_type, scopes, last)
        ds = matrix["directory_sync"]
        if not ds["available"]:
            await self._save_sync_state(
                db, tenant_id, provider,
                status="not_applicable", total=0, error=ds["reason"],
            )
            return {"created": 0, "updated": 0, "skipped": 0, "total": 0,
                    "status": "not_applicable", "reason": ds["reason"]}
        return None
```

At the **start** of `sync_google_users` (right after `token = await get_fresh_token(...)` and its `if not token` guard, before the `async with httpx.AsyncClient()`), add:

```python
        blocked = await self._directory_sync_blocked(db, tenant_id, "google")
        if blocked is not None:
            return blocked
```

Add the identical guard at the start of `sync_microsoft_users` with `"microsoft"`.

- [ ] **Step 4: Run to verify tests pass**

Run: `py -m pytest tests/test_user_sync_state.py -v`
Expected: PASS (new tests + all existing — the existing Workspace/`workspace`-less tests use `account_type=None`; confirm they still pass. Note: existing google/microsoft sync tests create creds **without** `account_type`, so the resolver returns `unknown` → directory_sync unavailable → they would now short-circuit. To preserve their intent, update those existing fixtures to set `account_type="workspace"` for google and `account_type="azure_ad"` for microsoft.)

- [ ] **Step 5: Update existing sync test fixtures**

In `backend/tests/test_user_sync_state.py`, for every existing `TenantCredential(...)` that is exercised through `sync_microsoft_users`/`sync_google_users` and expects a real sync (the MS tests and `test_google_sync_avoids_directory_query_filter_and_skips_suspended`), add `account_type="azure_ad"` (microsoft) or `account_type="workspace"` (google) to the constructor. The `test_permissions_*` tests don't call sync and need no change.

- [ ] **Step 6: Run full sync test module**

Run: `py -m pytest tests/test_user_sync_state.py tests/test_user_sync_scheduler.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/user_sync.py backend/tests/test_user_sync_state.py
git commit -m "feat: record directory sync as not_applicable on incompatible tiers"
```

---

## Task 6: API surface — permissions audit + /status

**Files:**
- Modify: `backend/app/schemas/integrations.py`
- Modify: `backend/app/routers/integrations.py` (`integration_status` ~line 683-718)
- Modify: `backend/app/routers/admin.py` (`get_permissions_audit` ~line 1188-1230)
- Test: `backend/tests/test_user_sync_state.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_user_sync_state.py`:

```python
@pytest.mark.asyncio
async def test_permissions_reports_tier_and_capabilities(client, db_session, test_tenant):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token="enc",
            scopes="https://www.googleapis.com/auth/drive",
            is_active=True,
            account_type="personal",
            last_user_sync_status="not_applicable",
        )
    )
    await db_session.commit()

    resp = await client.get("/api/admin/permissions")
    assert resp.status_code == 200
    g = resp.json()["google"]
    assert g["account_type"] == "personal"
    assert g["account_label"] == "Personal Google (Gmail)"
    assert g["capabilities"]["directory_sync"]["available"] is False
    assert g["capabilities"]["cloud_storage"]["available"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `py -m pytest tests/test_user_sync_state.py::test_permissions_reports_tier_and_capabilities -v`
Expected: FAIL — `KeyError: 'account_type'`.

- [ ] **Step 3: Extend the permissions audit**

In `backend/app/routers/admin.py`, add the import near the top:

```python
from app.services import capabilities as cap
```

Inside `audit_provider`, extend both return dicts (the disconnected branch and the connected branch) to include tier + capabilities. Compute once before the returns:

```python
        account_type = match.account_type if match else None
        account_domain = match.account_domain if match else None
        scopes_str = match.scopes if match else None
        last_status = match.last_user_sync_status if match else None
        capabilities = cap.resolve(provider, account_type, scopes_str, last_status)
        tier = {
            "account_type": account_type,
            "account_domain": account_domain,
            "account_label": cap.account_label(provider, account_type),
            "capabilities": capabilities,
        }
```

Add `**tier` to **both** returned dicts (alongside `**freshness`).

- [ ] **Step 4: Add best-effort backfill for unknown tiers**

Still in `get_permissions_audit`, after loading `creds` and before `audit_provider` is called, backfill any connected credential whose `account_type` is unknown:

```python
    from app.services import account_detect
    from app.services.token_vault import get_fresh_token

    for c in creds:
        if c.scopes and c.account_type in (None, "unknown"):
            try:
                token = await get_fresh_token(db, tenant_id, c.provider)
                if token:
                    if c.provider == "google":
                        at, dom = await account_detect.backfill_google(token)
                    else:
                        at, dom = await account_detect.backfill_microsoft(token)
                    if at != "unknown":
                        account_detect.apply_detection(c, at, dom)
                        await db.commit()
            except Exception:
                logger.warning("tier backfill failed for %s", c.provider, exc_info=True)
```

(Ensure `logger` exists in `admin.py`; if not, add `import logging; logger = logging.getLogger(__name__)` near the top.)

- [ ] **Step 5: Extend `IntegrationStatus` schema + `/status`**

In `backend/app/schemas/integrations.py`, add to `IntegrationStatus`:

```python
    account_type: Optional[str] = None
    account_label: Optional[str] = None
    account_domain: Optional[str] = None
    capabilities: dict = {}
```

In `backend/app/routers/integrations.py` `integration_status`, populate these on both `ms_status` and `google_status` using `cap.resolve(...)` + `cap.account_label(...)` (add `from app.services import capabilities as cap` import). Mirror the values from the credential rows (`ms_row` / `google_row`).

- [ ] **Step 6: Run tests**

Run: `py -m pytest tests/test_user_sync_state.py -v`
Expected: PASS, including the new permissions test and the existing `test_permissions_*` tests (they don't set `account_type`, so `account_label` is "Google (tier unknown)"; they only assert sync/health fields, unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/integrations.py backend/app/routers/integrations.py backend/app/routers/admin.py backend/tests/test_user_sync_state.py
git commit -m "feat: surface account tier + capability matrix in integrations APIs"
```

---

## Task 7: Frontend — tier badge + capability matrix

**Files:**
- Modify: `frontend/src/components/IntegrationsPanel.jsx` (`ProviderCard` ~line 280-308)

No automated test (no frontend test harness in scope). Manual verification step included.

- [ ] **Step 1: Add a feature-label map**

Near the top of `frontend/src/components/IntegrationsPanel.jsx` (with the other label consts), add:

```javascript
const CAPABILITY_LABELS = {
  directory_sync: 'Directory / user sync',
  cloud_storage: 'Cloud file storage',
  email: 'Email',
  calendar: 'Calendar',
  teams: 'Microsoft Teams',
}

const CAP_BADGE = {
  ok: { text: 'Available', cls: 'bg-green-100 text-green-700' },
  needs_reauth: { text: 'Reconnect needed', cls: 'bg-amber-100 text-amber-700' },
  error: { text: 'Error', cls: 'bg-red-100 text-red-700' },
  unavailable: { text: 'Not on this tier', cls: 'bg-gray-100 text-gray-500' },
}
```

- [ ] **Step 2: Show the tier badge + fix the sync line**

In `ProviderCard`, replace the connected-status paragraph (line ~298-303) with:

```jsx
          {info.account_label && (
            <span className="inline-block mt-1 ml-2 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
              {info.account_label}
            </span>
          )}
          {info.connected && (
            <p className="mt-1 text-xs text-brand-ink-2 font-sans">
              {info.user_count ?? 0} users synced
              {info.last_sync_status === 'failed'
                ? ' · last sync failed'
                : info.last_sync_status === 'not_applicable'
                ? ' · directory sync not available on this tier'
                : ` · last run ${relTime(info.last_sync_at)}`}
            </p>
          )}
          {info.connected && info.last_sync_error && info.last_sync_status === 'failed' && (
            <p className="mt-1 text-xs text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
              {info.last_sync_error}
            </p>
          )}
```

(The `last_sync_error` banner now only renders for genuine `failed` status, not `not_applicable`.)

- [ ] **Step 3: Render the capability matrix**

Inside `ProviderCard`, after the scopes list block (the `<div className="space-y-1.5">` section, ~line 329 onward — place it after that div closes), add:

```jsx
      {info.connected && info.capabilities && (
        <div className="mt-4 pt-4 border-t border-brand-line">
          <p className="text-xs font-bold text-brand-ink mb-2 font-sans">Features on this account</p>
          <div className="space-y-1.5">
            {Object.entries(info.capabilities).map(([key, capInfo]) => {
              const badge = CAP_BADGE[capInfo.status] || CAP_BADGE.unavailable
              return (
                <div key={key} className="flex items-center justify-between gap-3" title={capInfo.reason}>
                  <span className="text-xs text-brand-ink-2 font-sans">{CAPABILITY_LABELS[key] || key}</span>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge.cls}`}>
                    {badge.text}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
```

- [ ] **Step 4: Manual verification**

Run the frontend (`npm run dev` in `frontend/`) or rely on the deployed build. As an admin on a personal-Google tenant, open Admin → Integrations and confirm:
- The Google card shows the "Personal Google (Gmail)" tier badge.
- The status line reads "… · directory sync not available on this tier" (no red "last sync failed").
- The capability matrix lists Directory/user sync = "Not on this tier", and Cloud file storage / Email / Calendar = "Available".

Run a lint/build sanity check: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/IntegrationsPanel.jsx
git commit -m "feat: show account tier badge + capability matrix in admin integrations"
```

---

## Task 8: Migration smoke test + docs

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Verify the migration applies cleanly**

Run (against a scratch/test DB, not production):
`cd backend && py -m alembic upgrade head && py -m alembic downgrade -1 && py -m alembic upgrade head`
Expected: 056 applies and rolls back without error.

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && py -m pytest -q`
Expected: PASS (no regressions across the suite).

- [ ] **Step 3: Update CHANGELOG.md**

Add under `## [Unreleased] → ### Added`:

```markdown
- **Provider tier detection & capability matrix:** connected Google/Microsoft accounts are now classified as Workspace/Azure-AD vs personal (Gmail/MSA) from id_token claims (`hd`/`tid`), stored on `tenant_credentials` (migration `056_account_tier`). A pure capability resolver (`services/capabilities.py`) reports which features each tier supports. Directory sync on a personal account is recorded as `not_applicable` (not a hard failure); Admin → Integrations shows a tier badge and a per-feature availability matrix.
```

- [ ] **Step 4: Update TASKS.md**

Add a completed entry referencing this work (match the file's existing format/section for current work).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md TASKS.md
git commit -m "docs: changelog + tasks for provider tier detection"
```

---

## Self-Review

- **Spec coverage:** Detection (Task 3 + 4), data model (Task 1), capability resolver (Task 2), honest sync `not_applicable` (Task 5), API fields + backfill (Task 6), UI badge + matrix (Task 7), testing throughout, rollout/migration (Task 8). All spec sections map to tasks.
- **Type consistency:** `resolve(provider, account_type, scopes, last_sync_status)` and `account_label(provider, account_type)` used identically in Tasks 2/5/6. `apply_detection(cred, account_type, account_domain)` used in Tasks 3/4/6. `detect_google`/`detect_microsoft` return `(account_type, domain)` consistently. Capability dict keys (`directory_sync`, `cloud_storage`, `email`, `calendar`, `teams`) and entry shape (`available`/`status`/`reason`) consistent across resolver, API, and frontend.
- **Placeholder scan:** none — all steps contain concrete code/commands.
- **Known seam:** existing sync tests create creds without `account_type`; Task 5 Step 5 explicitly updates them so they exercise the real sync path rather than short-circuiting.

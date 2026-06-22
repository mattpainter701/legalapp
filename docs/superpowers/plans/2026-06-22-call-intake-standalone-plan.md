# Call Intake Standalone Plan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sell Call Intake standalone — provision a tenant locked to the intake dashboard with exportable call + partner-assignment logs, on a reusable plan/tier framework with an upsell path.

**Architecture:** A backend plan registry drives module visibility, a JWT-claim-based guard enforces module access at the API, an append-only `PartnerAssignmentLog` records every assignment, two provisioning entry points (operator toggle + public signup) name a plan, and the frontend gates nav with locked-item upsell teasers.

**Tech Stack:** FastAPI, SQLAlchemy (async) + Postgres RLS, Alembic, JWT (python-jose), React + Vite + Tailwind, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-22-call-intake-standalone-plan-design.md`

**Conventions:** Python `py` launcher on Windows; tests run from `backend/` with `py -m pytest`. Commit messages imperative, no `Co-Authored-By`. Work on a feature branch, not `main`.

---

## Phase 0 — Baseline

### Task 0: Branch + commit the in-progress intake changes

The working tree has a complete, tested `specific_staff` general-task feature (intake router +201, schemas, tests, page). Land it before building on top.

**Files:** `backend/app/routers/intake_dashboard.py`, `backend/app/schemas/intake_dashboard.py`, `backend/tests/test_intake_dashboard.py`, `frontend/src/pages/IntakeDashboardPage.jsx`

- [ ] **Step 1: Branch**

```bash
git checkout -b feat/call-intake-standalone
```

- [ ] **Step 2: Run the existing intake tests to confirm the baseline is green**

Run: `cd backend && py -m pytest tests/test_intake_dashboard.py -q`
Expected: PASS (all existing intake tests, including the new `specific_staff` test).

- [ ] **Step 3: Commit the baseline**

```bash
git add backend/app/routers/intake_dashboard.py backend/app/schemas/intake_dashboard.py backend/tests/test_intake_dashboard.py frontend/src/pages/IntakeDashboardPage.jsx
git commit -m "feat: add specific-staff general task mode to intake dashboard"
```

- [ ] **Step 4: Commit the design + plan docs**

```bash
git add docs/superpowers/specs/2026-06-22-call-intake-standalone-plan-design.md docs/superpowers/plans/2026-06-22-call-intake-standalone-plan.md
git commit -m "docs: add call-intake standalone plan spec and implementation plan"
```

---

## Phase 1 — Plan registry + module visibility

### Task 1: Plan registry

**Files:**
- Create: `backend/app/services/plans.py`
- Test: `backend/tests/test_plans.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_plans.py
from app.services.plans import PLANS, get_plan, public_plans, plan_for_config


def test_intake_only_plan_shape():
    plan = get_plan("intake-only")
    assert plan.modules == ["intake-dashboard"]
    assert plan.default_module == "intake-dashboard"
    assert plan.public_signup is True
    assert plan.upsell_target == "full-platform"
    assert plan.billing_tier == "intake_trial"


def test_full_platform_is_not_public():
    assert get_plan("full-platform").public_signup is False


def test_public_plans_only_returns_signup_enabled():
    ids = {p.id for p in public_plans()}
    assert "intake-only" in ids
    assert "full-platform" not in ids


def test_plan_for_config_defaults_to_full_platform():
    assert plan_for_config(None).id == "full-platform"
    assert plan_for_config({}).id == "full-platform"
    assert plan_for_config({"plan": "intake-only"}).id == "intake-only"
    assert plan_for_config({"plan": "bogus"}).id == "full-platform"


def test_get_plan_unknown_returns_none():
    assert get_plan("nope") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && py -m pytest tests/test_plans.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.plans`.

- [ ] **Step 3: Implement the registry**

```python
# backend/app/services/plans.py
"""Sellable plan registry — single source of truth for module bundles/tiers."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.module_visibility import FULL_PLATFORM_MODULES


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    modules: list[str]
    default_module: str
    billing_tier: str
    public_signup: bool
    upsell_target: str | None


PLANS: dict[str, Plan] = {
    "intake-only": Plan(
        id="intake-only",
        label="Call Intake",
        modules=["intake-dashboard"],
        default_module="intake-dashboard",
        billing_tier="intake_trial",
        public_signup=True,
        upsell_target="full-platform",
    ),
    "full-platform": Plan(
        id="full-platform",
        label="Full Platform",
        modules=list(FULL_PLATFORM_MODULES),
        default_module="matters",
        billing_tier="payg",
        public_signup=False,
        upsell_target=None,
    ),
}

DEFAULT_PLAN_ID = "full-platform"


def get_plan(plan_id: str | None) -> Plan | None:
    if not plan_id:
        return None
    return PLANS.get(plan_id)


def public_plans() -> list[Plan]:
    return [p for p in PLANS.values() if p.public_signup]


def plan_for_config(custom_config: dict | None) -> Plan:
    plan = get_plan((custom_config or {}).get("plan"))
    return plan or PLANS[DEFAULT_PLAN_ID]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd backend && py -m pytest tests/test_plans.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/plans.py backend/tests/test_plans.py
git commit -m "feat: add plan registry for sellable module bundles"
```

### Task 2: Drive module_visibility from the registry

Replace the hardcoded `intake-only` block with a registry lookup; add a helper that returns plan id + upsell target for the auth payload. Preserve every existing fallback.

**Files:**
- Modify: `backend/app/services/module_visibility.py`
- Test: `backend/tests/test_licensing_access.py` (add cases), `backend/tests/test_plans.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to backend/tests/test_plans.py
import uuid

import pytest

from app.services.module_visibility import resolve_enabled_modules, resolve_plan_meta
from app.models.tenant import TenantSettings


@pytest.mark.asyncio
async def test_resolve_intake_only_from_plan(db_session, test_tenant, test_user):
    db_session.add(TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "intake-only"}))
    await db_session.commit()
    modules, route = await resolve_enabled_modules(db_session, test_tenant.id, user=test_user)
    # admin user also gets the admin module via _with_finance_admin
    assert "intake-dashboard" in modules
    assert "plugins" not in modules
    assert route == "/intake/dashboard"


@pytest.mark.asyncio
async def test_resolve_plan_meta_exposes_upsell(db_session, test_tenant):
    db_session.add(TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "intake-only"}))
    await db_session.commit()
    plan_id, upsell = await resolve_plan_meta(db_session, test_tenant.id)
    assert plan_id == "intake-only"
    assert upsell == "full-platform"


@pytest.mark.asyncio
async def test_resolve_no_config_is_full_platform(db_session, test_tenant):
    plan_id, upsell = await resolve_plan_meta(db_session, test_tenant.id)
    assert plan_id == "full-platform"
    assert upsell is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_plans.py -q`
Expected: FAIL — `resolve_plan_meta` undefined; intake-only test may still see `plugins`.

- [ ] **Step 3: Refactor `module_visibility.py`**

Replace the `intake-only` branch in `resolve_enabled_modules` and add `resolve_plan_meta`. The `plan_for_config` import is done lazily inside the functions to avoid a circular import (`plans.py` imports `FULL_PLATFORM_MODULES` from this module).

```python
# In resolve_enabled_modules, REPLACE this block:
#
#     if custom_config.get("plan") == "intake-only":
#         enabled = _with_finance_admin(INTAKE_ONLY_MODULES, user)
#         return enabled, MODULE_ROUTES["intake-dashboard"]
#
# WITH:
    from app.services.plans import get_plan  # lazy import avoids cycle

    plan = get_plan(custom_config.get("plan"))
    if plan is not None:
        enabled = _with_finance_admin(list(plan.modules), user)
        default_module = plan.default_module if plan.default_module in enabled else enabled[0]
        return enabled, MODULE_ROUTES[default_module]
```

```python
# Add at end of module_visibility.py:
async def resolve_plan_meta(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[str, str | None]:
    """Return (plan_id, upsell_target) for the auth payload."""
    from app.services.plans import plan_for_config

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = result.scalar_one_or_none()
    plan = plan_for_config(ts.custom_config if ts else None)
    return plan.id, plan.upsell_target
```

Leave `INTAKE_ONLY_MODULES` defined (now unused by resolve, but referenced nowhere else — delete it and `if not enabled: enabled = ["intake-dashboard"]` stays as the safety net).

- [ ] **Step 4: Run to verify pass + no regressions**

Run: `cd backend && py -m pytest tests/test_plans.py tests/test_licensing_access.py -q`
Expected: PASS (new + existing licensing tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/module_visibility.py backend/tests/test_plans.py
git commit -m "feat: drive module visibility from plan registry"
```

---

## Phase 2 — Backend module guard (fail-closed)

### Task 3: API module-access guard via JWT plan claim

Add a `plan` claim to issued tokens, then a middleware that 403s requests to module-scoped API prefixes outside the tenant's plan. Server-signed claim → trustworthy; absent claim → full-platform (backward compatible).

**Files:**
- Modify: `backend/app/routers/auth.py:161` (`_create_access_token`)
- Create: `backend/app/middleware/module_guard.py`
- Modify: `backend/app/main.py` (register middleware after `TenantMiddleware`)
- Test: `backend/tests/test_module_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_module_guard.py
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.database import get_db
from app.main import app

settings = get_settings()


def _token(tenant_id, user_id, plan):
    payload = {
        "sub": str(user_id), "tenant_id": str(tenant_id), "role": "admin",
        "email": "a@b.com", "billing_tier": "intake_trial", "plan": plan,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest_asyncio.fixture
async def intake_client(db_session, test_tenant, test_user):
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    token = _token(test_tenant.id, test_user.id, "intake-only")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_intake_only_blocked_from_matters(intake_client):
    resp = await intake_client.get("/api/matters")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Module not available on your plan"


@pytest.mark.asyncio
async def test_intake_only_allowed_on_intake(intake_client):
    resp = await intake_client.get("/api/intake/dashboard/recent-callers", params={"limit": 10})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_full_platform_token_not_blocked(client):
    # default conftest token has no plan claim -> full-platform -> allowed
    resp = await client.get("/api/matters")
    assert resp.status_code != 403
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_module_guard.py -q`
Expected: FAIL (matters returns 200, not 403 — guard not installed).

- [ ] **Step 3: Add the `plan` claim to tokens**

In `backend/app/routers/auth.py` `_create_access_token`, resolve and add the plan claim. Add a small helper near the token helpers:

```python
# In _create_access_token, build payload with the plan claim:
from app.services.plans import plan_for_config  # top-of-file import

# inside _create_access_token, replace the payload dict's construction to include:
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "plan": plan_for_config(getattr(tenant, "_plan_config", None)).id,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
```

`tenant._plan_config` isn't a column; instead resolve from TenantSettings. Since `_create_access_token` is sync and has no db, pass the plan id in. Change the signature to accept `plan_id: str = "full-platform"` and have callers compute it. Find each `_create_access_token(user, tenant)` call (login, oauth, refresh) and pass the resolved plan:

```python
def _create_access_token(user: User, tenant: Tenant, plan_id: str = "full-platform") -> str:
    ...
        "plan": plan_id,
    ...

# At each call site (login/oauth/refresh), after loading TenantSettings:
from app.services.module_visibility import resolve_plan_meta
plan_id, _ = await resolve_plan_meta(db, user.tenant_id)
token = _create_access_token(user, tenant, plan_id)
```

- [ ] **Step 4: Implement the guard middleware**

```python
# backend/app/middleware/module_guard.py
"""Fail-closed API module enforcement keyed off the JWT plan claim."""

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError, jwt

from app.config import get_settings
from app.services.plans import get_plan, DEFAULT_PLAN_ID

settings = get_settings()

# Module-scoped API prefixes. Anything not listed (auth, me, users, notifications,
# intake, admin, plugins listing, health, portal) is shared infra and passes.
API_MODULE_MAP = {
    "/api/matters": "matters",
    "/api/chat": "chat",
    "/api/calendar": "calendar",
    "/api/communications": "communications",
    "/api/contacts": "contacts",
    "/api/templates": "templates",
    "/api/time-tracking": "time-tracking",
    "/api/invoices": "invoices",
    "/api/trust": "trust",
    "/api/reports": "reports",
    "/api/mcp": "mcp",
}


def _required_module(path: str) -> str | None:
    for prefix, module in API_MODULE_MAP.items():
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


class ModuleGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        module = _required_module(request.url.path)
        if module is None:
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            auth = request.headers.get("Authorization", "")
            token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else None
        if not token:
            return await call_next(request)  # unauthenticated -> let auth deps 401

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except JWTError:
            return await call_next(request)

        plan = get_plan(payload.get("plan")) or get_plan(DEFAULT_PLAN_ID)
        role = payload.get("role")
        allowed = set(plan.modules)
        if role in {"admin", "accountant"}:
            allowed.add("admin")
        if module not in allowed:
            return JSONResponse(status_code=403, content={"detail": "Module not available on your plan"})
        return await call_next(request)
```

- [ ] **Step 5: Register the middleware**

In `backend/app/main.py`, after the `TenantMiddleware` is added:

```python
from app.middleware.module_guard import ModuleGuardMiddleware
app.add_middleware(ModuleGuardMiddleware)
```

(Starlette runs middleware in reverse-add order; adding after TenantMiddleware means the guard runs as an outer layer, which is fine — it only reads the token.)

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && py -m pytest tests/test_module_guard.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Full auth regression**

Run: `cd backend && py -m pytest tests/test_licensing_access.py tests/test_module_guard.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/middleware/module_guard.py backend/app/main.py backend/app/routers/auth.py backend/tests/test_module_guard.py
git commit -m "feat: enforce plan module access at the API (fail-closed)"
```

---

## Phase 3 — Partner assignment log

### Task 4: Model + migration 064

**Files:**
- Modify: `backend/app/models/intake_dashboard.py`
- Create: `backend/migrations/versions/064_partner_assignment_log.py`

- [ ] **Step 1: Add the model**

```python
# Append to backend/app/models/intake_dashboard.py
class PartnerAssignmentLog(Base):
    """Append-only record of every partner/staff assignment event."""

    __tablename__ = "partner_assignment_log"
    __table_args__ = (
        Index("idx_partner_assignment_log_tenant", "tenant_id"),
        Index("idx_partner_assignment_log_created", "tenant_id", "created_at"),
        Index("idx_partner_assignment_log_assignee", "tenant_id", "assigned_to_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    communication_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    practice_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    assigned_to_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rotation_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assignment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    assigned_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc), server_default="now()",
    )
```

- [ ] **Step 2: Write the migration** (mirror `061`'s `_enable_rls`)

```python
# backend/migrations/versions/064_partner_assignment_log.py
"""Add partner assignment log.

Revision ID: 064
Revises: 063
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "partner_assignment_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("communication_id", UUID(as_uuid=True), nullable=True),
        sa.Column("practice_area", sa.String(100), nullable=True),
        sa.Column("assigned_to_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_to_name", sa.String(255), nullable=True),
        sa.Column("rotation_rule_id", UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_method", sa.String(50), nullable=False),
        sa.Column("assigned_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_partner_assignment_log_tenant", "partner_assignment_log", ["tenant_id"])
    op.create_index("idx_partner_assignment_log_created", "partner_assignment_log", ["tenant_id", "created_at"])
    op.create_index("idx_partner_assignment_log_assignee", "partner_assignment_log", ["tenant_id", "assigned_to_user_id"])
    _enable_rls("partner_assignment_log")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS partner_assignment_log_tenant_isolation ON partner_assignment_log")
    op.drop_index("idx_partner_assignment_log_assignee", table_name="partner_assignment_log")
    op.drop_index("idx_partner_assignment_log_created", table_name="partner_assignment_log")
    op.drop_index("idx_partner_assignment_log_tenant", table_name="partner_assignment_log")
    op.drop_table("partner_assignment_log")
```

- [ ] **Step 3: Apply migration against the test/dev DB**

Run: `cd backend && py -m alembic upgrade head`
Expected: `Running upgrade 063 -> 064`. (If the test suite builds schema from models via `create_all`, this still validates the migration runs.)

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/intake_dashboard.py backend/migrations/versions/064_partner_assignment_log.py
git commit -m "feat: add partner_assignment_log table with RLS"
```

### Task 5: Record assignments + schemas

**Files:**
- Modify: `backend/app/routers/intake_dashboard.py`
- Modify: `backend/app/schemas/intake_dashboard.py`
- Test: `backend/tests/test_intake_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to backend/tests/test_intake_dashboard.py
@pytest.mark.asyncio
async def test_partner_assignment_is_logged_on_assign_next(
    client, db_session, test_tenant, test_user
):
    from app.models.intake_dashboard import PartnerAssignmentLog, PartnerRotationState
    from app.models.contact import Contact, Lead
    from sqlalchemy import select

    partner = User(id=uuid.uuid4(), tenant_id=test_tenant.id, email="p@f.com",
                   full_name="Pat Partner", role="user", is_active=True)
    contact = Contact(tenant_id=test_tenant.id, contact_type="prospect",
                      first_name="Lee", last_name="Caller")
    db_session.add_all([partner, contact])
    await db_session.commit()
    lead = Lead(tenant_id=test_tenant.id, contact_id=contact.id, status="new",
                source="phone", practice_area="divorce")
    db_session.add(lead)
    db_session.add(PartnerRotationState(tenant_id=test_tenant.id, practice_area="divorce",
                  eligible_user_ids=[str(partner.id)], is_enabled=True))
    await db_session.commit()

    resp = await client.post(f"/api/intake/dashboard/leads/{lead.id}/assign-next")
    assert resp.status_code == 200

    rows = (await db_session.execute(
        select(PartnerAssignmentLog).where(PartnerAssignmentLog.tenant_id == test_tenant.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].assignment_method == "partner_rotation"
    assert rows[0].assigned_to_name == "Pat Partner"
    assert rows[0].lead_id == lead.id
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_intake_dashboard.py::test_partner_assignment_is_logged_on_assign_next -q`
Expected: FAIL (no log row).

- [ ] **Step 3: Add the recorder helper + call it**

Add the import `from app.models.intake_dashboard import LegacyCallRecord, PartnerRotationState, PartnerAssignmentLog`. Add helper:

```python
async def record_partner_assignment(
    db: AsyncSession, *, tenant_id, assignment_method, assigned_to_user_id,
    assigned_to_name, assigned_by_user_id, assigned_by_name=None,
    lead_id=None, contact_id=None, communication_id=None,
    practice_area=None, rotation_rule_id=None,
) -> None:
    db.add(PartnerAssignmentLog(
        tenant_id=tenant_id, assignment_method=assignment_method,
        assigned_to_user_id=assigned_to_user_id, assigned_to_name=assigned_to_name,
        assigned_by_user_id=assigned_by_user_id, assigned_by_name=assigned_by_name,
        lead_id=lead_id, contact_id=contact_id, communication_id=communication_id,
        practice_area=practice_area, rotation_rule_id=rotation_rule_id,
    ))
```

Call it in `assign_next_partner` (before `await db.commit()`):

```python
    await record_partner_assignment(
        db, tenant_id=tenant_id, assignment_method="partner_rotation",
        assigned_to_user_id=selected_id,
        assigned_to_name=selected_user.full_name or selected_user.email,
        assigned_by_user_id=current_user.id,
        assigned_by_name=current_user.full_name or current_user.email,
        lead_id=lead.id, contact_id=lead.contact_id,
        practice_area=rule.practice_area, rotation_rule_id=rule.id,
    )
```

And in `create_dashboard_call`, after a lead is assigned via rotation (partner_rotation branch where `lead_assignee`/recommended attorney is set) and in the `specific_staff` branch after the general task is created — record with `assignment_method="partner_rotation"` / `"prior_attorney"` / `"specific_staff"` respectively, using the resolved assignee's snapshot name, before `await db.commit()`.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && py -m pytest tests/test_intake_dashboard.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/intake_dashboard.py backend/app/schemas/intake_dashboard.py backend/tests/test_intake_dashboard.py
git commit -m "feat: record partner assignment events to partner log"
```

### Task 6: Partner-log list + export endpoints

**Files:**
- Modify: `backend/app/routers/intake_dashboard.py`, `backend/app/schemas/intake_dashboard.py`
- Test: `backend/tests/test_intake_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to backend/tests/test_intake_dashboard.py
@pytest.mark.asyncio
async def test_partner_log_list_and_export(client, db_session, test_tenant, test_user):
    from app.models.intake_dashboard import PartnerAssignmentLog
    db_session.add(PartnerAssignmentLog(
        tenant_id=test_tenant.id, assignment_method="partner_rotation",
        assigned_to_name="Pat Partner", assigned_by_name="Reception",
        practice_area="divorce",
    ))
    await db_session.commit()

    listing = await client.get("/api/intake/dashboard/partner-log")
    assert listing.status_code == 200
    assert listing.json()["entries"][0]["assigned_to_name"] == "Pat Partner"

    export = await client.get("/api/intake/dashboard/partner-log/export")
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert rows[0]["assigned_to_name"] == "Pat Partner"
    assert rows[0]["assignment_method"] == "partner_rotation"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_intake_dashboard.py::test_partner_log_list_and_export -q`
Expected: FAIL (404 — endpoints missing).

- [ ] **Step 3: Add schemas + endpoints**

Schemas in `schemas/intake_dashboard.py`:

```python
class PartnerAssignmentLogEntry(BaseModel):
    id: uuid.UUID
    created_at: datetime
    assignment_method: str
    assigned_to_user_id: Optional[uuid.UUID] = None
    assigned_to_name: Optional[str] = None
    assigned_by_name: Optional[str] = None
    practice_area: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    communication_id: Optional[uuid.UUID] = None
    model_config = ConfigDict(from_attributes=True)


class PartnerAssignmentLogResponse(BaseModel):
    entries: list[PartnerAssignmentLogEntry]
```

Endpoints in `routers/intake_dashboard.py` (date-range + optional `assigned_to_user_id` filter; export reuses a CSV writer with `PARTNER_LOG_EXPORT_FIELDS = ["created_at","assignment_method","assigned_to_name","assigned_by_name","practice_area","lead_id","contact_id","communication_id"]`):

```python
@router.get("/partner-log", response_model=PartnerAssignmentLogResponse)
async def list_partner_log(
    start: date | None = Query(None), end: date | None = Query(None),
    assigned_to_user_id: uuid.UUID | None = Query(None), limit: int = 200,
    current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    filters = [PartnerAssignmentLog.tenant_id == tenant_id]
    if start:
        filters.append(PartnerAssignmentLog.created_at >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        filters.append(PartnerAssignmentLog.created_at <= datetime.combine(end, time.max, tzinfo=timezone.utc))
    if assigned_to_user_id:
        filters.append(PartnerAssignmentLog.assigned_to_user_id == assigned_to_user_id)
    rows = (await db.execute(
        select(PartnerAssignmentLog).where(*filters)
        .order_by(PartnerAssignmentLog.created_at.desc()).limit(limit)
    )).scalars().all()
    return PartnerAssignmentLogResponse(
        entries=[PartnerAssignmentLogEntry.model_validate(r) for r in rows]
    )


@router.get("/partner-log/export")
async def export_partner_log(
    start: date | None = Query(None), end: date | None = Query(None),
    current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    filters = [PartnerAssignmentLog.tenant_id == tenant_id]
    if start:
        filters.append(PartnerAssignmentLog.created_at >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        filters.append(PartnerAssignmentLog.created_at <= datetime.combine(end, time.max, tzinfo=timezone.utc))
    rows = (await db.execute(
        select(PartnerAssignmentLog).where(*filters)
        .order_by(PartnerAssignmentLog.created_at.desc())
    )).scalars().all()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PARTNER_LOG_EXPORT_FIELDS)
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "created_at": _iso_datetime(r.created_at),
            "assignment_method": r.assignment_method,
            "assigned_to_name": r.assigned_to_name or "",
            "assigned_by_name": r.assigned_by_name or "",
            "practice_area": r.practice_area or "",
            "lead_id": str(r.lead_id) if r.lead_id else "",
            "contact_id": str(r.contact_id) if r.contact_id else "",
            "communication_id": str(r.communication_id) if r.communication_id else "",
        })
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=partner-log.csv"})
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && py -m pytest tests/test_intake_dashboard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/intake_dashboard.py backend/app/schemas/intake_dashboard.py backend/tests/test_intake_dashboard.py
git commit -m "feat: add partner log list and CSV export endpoints"
```

---

## Phase 4 — Provisioning

### Task 7: Operator toggle

**Files:**
- Modify: `backend/app/routers/platform.py` (`TenantUpdate`, `update_tenant`, tenant detail payload; add `GET /plans`)
- Test: `backend/tests/test_platform.py` (create if absent — follow existing platform test patterns; auth via `X-Platform-Key`)

- [ ] **Step 1: Write the failing test** — set `plan` on a tenant, expect `custom_config.plan` persisted; `GET /api/platform/plans` lists `intake-only`; unknown plan → 400.

```python
# backend/tests/test_platform.py (add)
@pytest.mark.asyncio
async def test_set_tenant_plan(platform_client, db_session, test_tenant):
    from app.models.tenant import TenantSettings
    from sqlalchemy import select
    resp = await platform_client.put(f"/api/platform/tenants/{test_tenant.id}", json={"plan": "intake-only"})
    assert resp.status_code == 200
    ts = (await db_session.execute(select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id))).scalar_one()
    assert ts.custom_config["plan"] == "intake-only"


@pytest.mark.asyncio
async def test_set_unknown_plan_rejected(platform_client, test_tenant):
    resp = await platform_client.put(f"/api/platform/tenants/{test_tenant.id}", json={"plan": "bogus"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_plans(platform_client):
    resp = await platform_client.get("/api/platform/plans")
    assert resp.status_code == 200
    assert any(p["id"] == "intake-only" for p in resp.json()["plans"])
```

(If no `platform_client` fixture exists, add one that sets the platform key header the same way existing platform endpoints authenticate — check `_require_platform_key` in `platform.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_platform.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

- Add `plan: Optional[str] = None` to `TenantUpdate`.
- In `update_tenant`, after the module_config block, handle `plan`:

```python
    if _field_was_sent(body, "plan"):
        from app.services.plans import get_plan
        if get_plan(body.plan) is None:
            raise HTTPException(status_code=400, detail=f"Unknown plan '{body.plan}'")
        ts_result = await db.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant.id))
        ts = ts_result.scalar_one_or_none()
        if ts is None:
            ts = TenantSettings(tenant_id=tenant.id); db.add(ts); await db.flush()
        custom_config = dict(ts.custom_config or {})
        custom_config["plan"] = body.plan
        ts.custom_config = custom_config
```

- Add the listing endpoint:

```python
@router.get("/plans")
async def list_plans(request: Request):
    _require_platform_key(request)
    from app.services.plans import PLANS
    return {"plans": [
        {"id": p.id, "label": p.label, "modules": p.modules,
         "public_signup": p.public_signup, "upsell_target": p.upsell_target}
        for p in PLANS.values()
    ]}
```

- Add `"plan": (ts.custom_config or {}).get("plan")` to the tenant detail `module_config` payload.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && py -m pytest tests/test_platform.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/platform.py backend/tests/test_platform.py
git commit -m "feat: operator plan toggle and plan listing"
```

### Task 8: Public self-serve signup

**Files:**
- Modify: `backend/app/routers/auth.py` (new `POST /api/auth/signup/plan`), `backend/app/schemas/auth.py`
- Test: `backend/tests/test_auth_signup_plan.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_auth_signup_plan.py
import pytest


@pytest.mark.asyncio
async def test_public_signup_provisions_intake_tenant(public_client, db_session):
    from app.models.tenant import Tenant, TenantSettings
    from app.models.user import User
    from sqlalchemy import select
    resp = await public_client.post("/api/auth/signup/plan", json={
        "plan": "intake-only", "firm_name": "Reception Co",
        "email": "owner@reception.co", "password": "longenoughpw123", "full_name": "Owner One",
    })
    assert resp.status_code == 201
    user = (await db_session.execute(select(User).where(User.email == "owner@reception.co"))).scalar_one()
    assert user.role == "admin"
    ts = (await db_session.execute(select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id))).scalar_one()
    assert ts.custom_config["plan"] == "intake-only"
    tenant = (await db_session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    assert tenant.billing_tier == "intake_trial"


@pytest.mark.asyncio
async def test_signup_rejects_non_public_plan(public_client):
    resp = await public_client.post("/api/auth/signup/plan", json={
        "plan": "full-platform", "firm_name": "X", "email": "x@y.co",
        "password": "longenoughpw123", "full_name": "X",
    })
    assert resp.status_code == 403
```

(`public_client` = AsyncClient with no auth header, get_db overridden — model it on the `client` fixture minus the Authorization header. `/api/auth/*` is already auth-exempt in middleware.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && py -m pytest tests/test_auth_signup_plan.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Implement**

Add schema `PlanSignupRequest(plan, firm_name, email: EmailStr, password, full_name)`. Add endpoint that: validates `get_plan(plan)` exists AND `public_signup` (else 403); creates `Tenant(name=firm_name, domain=<slug/uuid>, billing_tier=plan.billing_tier)`, `TenantSettings(custom_config={"plan": plan.id, "trial_ends_at": (now+14d).isoformat()})`, admin `User` (hash password via `_hash_password`, `license_active=True`), commit; return 201 with `{tenant_id, user_id}`. Reuse the existing `register` helper logic for tenant/user creation if one exists — otherwise mirror its construction. Generate `domain` uniquely (e.g. `f"{slug}-{uuid4().hex[:8]}"`).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && py -m pytest tests/test_auth_signup_plan.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/auth.py backend/app/schemas/auth.py backend/tests/test_auth_signup_plan.py
git commit -m "feat: public plan-based self-serve signup"
```

### Task 9: Upgrade-request endpoint (lead capture)

**Files:** Modify `backend/app/routers/auth.py` (or a small `routers/plan.py`); Test `backend/tests/test_upgrade_request.py`

- [ ] **Step 1: Failing test** — authenticated `POST /api/plan/upgrade-request {note}` returns 202 and writes an audit/notification row (reuse existing audit log model; assert a row exists).
- [ ] **Step 2:** Run → FAIL (404).
- [ ] **Step 3:** Implement: `POST /api/plan/upgrade-request` (auth required), record via existing audit-log/notification service with `action="plan_upgrade_request"`, tenant + user + optional note. Return 202.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: capture full-platform upgrade requests`.

---

## Phase 5 — Frontend

### Task 10: Expose plan + upsell in the auth payload

**Files:** Modify `backend/app/schemas/auth.py:44` (`UserInfo`), `backend/app/routers/auth.py:1242` (`/me`), `frontend/src/api.js` (if it maps user fields).

- [ ] **Step 1:** Add `plan: str = "full-platform"` and `upsell_target: Optional[str] = None` to `UserInfo`.
- [ ] **Step 2:** In `/me`, after `resolve_enabled_modules`, call `plan, upsell_target = await resolve_plan_meta(db, user.tenant_id)` and pass both to `UserInfo(...)`.
- [ ] **Step 3:** Run: `cd backend && py -m pytest tests/test_licensing_access.py -q` → PASS (payload still valid).
- [ ] **Step 4:** Commit `feat: expose plan and upsell target in user payload`.

### Task 11: Sidebar locked-nav + UpgradeModal

**Files:** Modify `frontend/src/components/Sidebar.jsx`; Create `frontend/src/components/UpgradeModal.jsx`; Modify `frontend/src/api.js` (add `requestUpgrade`).

- [ ] **Step 1:** In `Sidebar.jsx`, change the per-item filter: when `user.upsell_target` is set, do NOT drop disabled module items — instead mark them `locked: true`. Render locked items greyed with a `Lock` icon (lucide) and an `onClick` that opens `UpgradeModal` instead of navigating. Keep current hide-behavior when `upsell_target` is null.
- [ ] **Step 2:** Create `UpgradeModal.jsx`: headline "Upgrade to the full platform", a short feature list, and a "Request upgrade" button calling `requestUpgrade(note)` → `POST /api/plan/upgrade-request`; show a success state.
- [ ] **Step 3:** Add `requestUpgrade` to `api.js`.
- [ ] **Step 4:** Manual check: `cd frontend && npm run build` → builds clean.
- [ ] **Step 5:** Commit `feat: locked-nav upsell teasers with upgrade modal`.

### Task 12: Partner Log panel + export on the intake page

**Files:** Modify `frontend/src/pages/IntakeDashboardPage.jsx`, `frontend/src/api.js`.

- [ ] **Step 1:** Add `getPartnerLog(params)` and `downloadPartnerLogCsv(params)` to `api.js` (mirror the existing `getRecentIntakeDashboardCallers` / `downloadIntakeDashboardCallsCsv`).
- [ ] **Step 2:** Add a `PartnerLogPanel` section to `IntakeDashboardPage.jsx`: a list of recent assignment events (partner, method, practice area, when, by whom) + a date-range export button reusing the existing export UX pattern (`triggerBlobDownload`).
- [ ] **Step 3:** Manual check: `cd frontend && npm run build` → clean.
- [ ] **Step 4:** Commit `feat: partner log panel and export on intake dashboard`.

### Task 13: Operator UI plan dropdown

**Files:** Modify the operator/platform admin page (find via `grep enabled_modules` in `frontend/src` → the operator tenant editor).

- [ ] **Step 1:** Fetch `GET /api/platform/plans`; render a plan `<select>` on the tenant editor; on change, `PUT /tenants/{id}` with `{plan}`.
- [ ] **Step 2:** Manual check: build clean.
- [ ] **Step 3:** Commit `feat: operator plan selector`.

---

## Phase 6 — Wrap-up

### Task 14: Full suite + TASKS/CHANGELOG

- [ ] **Step 1:** Run the whole backend suite.

Run: `cd backend && py -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2:** Update `TASKS.md` (mark this work done with a summary) and `CHANGELOG.md` (Added/Changed/Tests for the plan framework, module guard, partner log, provisioning, upsell). If neither file exists, ask the user before creating.
- [ ] **Step 3:** Commit `docs: update TASKS and CHANGELOG for call-intake standalone`.
- [ ] **Step 4:** Offer to open a PR / deploy (do not push without the user asking).

---

## Self-review notes
- Spec sections 1–5 each map to tasks: §1→T1/T2, §2 provisioning→T7/T8, §3 partner log→T4/T5/T6, §4 UX/upsell→T10/T11/T12, §5 API guard→T3. Operator UI→T13; lead capture→T9; trial labeling in T8.
- Circular import (plans↔module_visibility) handled via lazy imports inside functions.
- Guard is fail-closed for mapped prefixes; absent JWT claim defaults to full-platform (documented backward-compat tradeoff; freshly-provisioned intake tenants always carry the claim).

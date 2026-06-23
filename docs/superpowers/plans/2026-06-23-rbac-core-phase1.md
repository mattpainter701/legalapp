# RBAC Core (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenant-scoped role registry whose roles carry a capability checklist, assign roles to users (union of capabilities), and migrate the two permission *dependencies* (`require_admin`, `require_finance_admin`) to consult capabilities — all behavior-preserving.

**Architecture:** New `roles` + `user_roles` tables. Capabilities are a fixed code-level catalog (not a DB table). A migration creates the tables and seeds four **system roles** per existing tenant (Administrator/Accountant/User/Client) plus backfills each user's current `user.role` into `user_roles(source='manual')`. Enforcement reads capabilities from the DB via a resolver; the legacy `user.role` column is retained so the ~40 inline `user.role != "admin"` checks keep working (migrated in a later pass). The login JWT gains a `caps` claim for `/me` and future middleware.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (Mapped), Alembic, Postgres (JSONB), pytest + httpx AsyncClient, React (Vite) for the admin tab.

**Compatibility boundary (read before starting):** Phase 1 migrates only `require_admin` and `require_finance_admin` to capabilities. Inline `user.role != "admin"` checks in routers (integrations.py, chat.py, calendar.py, etc.) are intentionally left reading the retained `user.role` column. Do NOT mass-migrate them in this plan.

---

### Task 1: Capability catalog

**Files:**
- Create: `backend/app/services/capabilities.py`
- Test: `backend/tests/test_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_capabilities.py
from app.services.capabilities import CAPABILITIES, is_valid_capability


def test_catalog_contains_core_capabilities():
    for cap in [
        "manage_users",
        "manage_roles",
        "manage_billing",
        "view_billing",
        "manage_matters",
        "manage_intake",
        "manage_documents",
        "manage_integrations",
        "admin_settings",
        "use_premium_ai",
    ]:
        assert cap in CAPABILITIES


def test_is_valid_capability():
    assert is_valid_capability("manage_roles") is True
    assert is_valid_capability("not_a_real_cap") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.capabilities`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/capabilities.py
"""Fixed catalog of grantable capabilities. NOT a DB table — roles store a
subset of these strings in roles.capabilities. Extend by adding here."""

from __future__ import annotations

CAPABILITIES: frozenset[str] = frozenset(
    {
        "manage_users",
        "manage_roles",
        "manage_billing",
        "view_billing",
        "manage_matters",
        "manage_intake",
        "manage_documents",
        "manage_integrations",
        "admin_settings",
        "use_premium_ai",
    }
)


def is_valid_capability(cap: str) -> bool:
    return cap in CAPABILITIES


# Capability sets for the four seeded system roles.
SYSTEM_ROLE_CAPABILITIES: dict[str, list[str]] = {
    "Administrator": sorted(CAPABILITIES),
    "Accountant": ["view_billing", "manage_billing"],
    "User": ["manage_matters", "manage_intake", "manage_documents"],
    "Client": [],
}

# Maps the legacy user.role value to the seeded system role name.
LEGACY_ROLE_TO_SYSTEM_ROLE: dict[str, str] = {
    "admin": "Administrator",
    "accountant": "Accountant",
    "user": "User",
    "client": "Client",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_capabilities.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/capabilities.py backend/tests/test_capabilities.py
git commit -m "feat: add RBAC capability catalog"
```

---

### Task 2: Role and UserRole models

**Files:**
- Create: `backend/app/models/rbac.py`
- Modify: `backend/app/models/__init__.py` (register new models)
- Test: `backend/tests/test_rbac_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rbac_models.py
import uuid

import pytest

from app.models.rbac import Role, UserRole
from app.models.user import User


@pytest.mark.asyncio
async def test_role_and_user_role_persist(db_session, test_tenant):
    role = Role(
        tenant_id=test_tenant.id,
        name="Paralegal",
        description="Paralegal staff",
        capabilities=["manage_matters", "manage_documents"],
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()

    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="para@testfirm.com",
        role="user",
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(UserRole(user_id=user.id, role_id=role.id, source="manual"))
    await db_session.commit()

    assert role.capabilities == ["manage_matters", "manage_documents"]
    assert role.is_system is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_rbac_models.py -v`
Expected: FAIL with `ModuleNotFoundError: app.models.rbac`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/models/rbac.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
        Index("idx_roles_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
        Index("idx_user_roles_user_id", "user_id"),
        Index("idx_user_roles_role_id", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # "manual" (admin-set, survives sync) or "group_sync" (Phase 2).
    source: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
```

Then register in `backend/app/models/__init__.py` — add alongside the existing imports/exports (follow the file's existing pattern):

```python
from app.models.rbac import Role, UserRole  # noqa: F401
```

If `__init__.py` has an `__all__` list, append `"Role"` and `"UserRole"` to it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_rbac_models.py -v`
Expected: PASS (1 passed). The `db_session`/`test_tenant` fixtures auto-create tables from `Base.metadata`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/rbac.py backend/app/models/__init__.py backend/tests/test_rbac_models.py
git commit -m "feat: add Role and UserRole models"
```

---

### Task 3: Alembic migration (tables + seed system roles + backfill)

**Files:**
- Create: `backend/migrations/versions/068_rbac_roles.py`

> Note: the latest revision is `067`. Set `down_revision = "067"`. The seed/backfill runs in raw SQL via `op.get_bind()`; migrations run as the table owner so forced RLS (migration 044) is bypassed.

- [ ] **Step 1: Write the migration**

```python
# backend/migrations/versions/068_rbac_roles.py
"""RBAC roles and user_roles, seed system roles, backfill from users.role.

Revision ID: 068
Revises: 067
Create Date: 2026-06-23
"""

import json
import uuid
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


SYSTEM_ROLE_CAPABILITIES = {
    "Administrator": [
        "manage_users", "manage_roles", "manage_billing", "view_billing",
        "manage_matters", "manage_intake", "manage_documents",
        "manage_integrations", "admin_settings", "use_premium_ai",
    ],
    "Accountant": ["view_billing", "manage_billing"],
    "User": ["manage_matters", "manage_intake", "manage_documents"],
    "Client": [],
}
LEGACY_ROLE_TO_SYSTEM_ROLE = {
    "admin": "Administrator",
    "accountant": "Accountant",
    "user": "User",
    "client": "Client",
}


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_system", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index("idx_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False,
                  server_default=sa.text("'manual'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index("idx_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("idx_user_roles_role_id", "user_roles", ["role_id"])

    bind = op.get_bind()
    tenants = bind.execute(sa.text("SELECT id FROM tenants")).fetchall()
    for (tenant_id,) in tenants:
        name_to_role_id: dict[str, str] = {}
        for role_name, caps in SYSTEM_ROLE_CAPABILITIES.items():
            role_id = str(uuid.uuid4())
            name_to_role_id[role_name] = role_id
            bind.execute(
                sa.text(
                    "INSERT INTO roles (id, tenant_id, name, description, "
                    "capabilities, is_system) VALUES (:id, :tid, :name, :descr, "
                    "CAST(:caps AS jsonb), true)"
                ),
                {
                    "id": role_id,
                    "tid": str(tenant_id),
                    "name": role_name,
                    "descr": f"System role: {role_name}",
                    "caps": json.dumps(caps),
                },
            )
        # Backfill each user's current role into user_roles(source='manual').
        users = bind.execute(
            sa.text("SELECT id, role FROM users WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        ).fetchall()
        for user_id, legacy_role in users:
            system_name = LEGACY_ROLE_TO_SYSTEM_ROLE.get(
                (legacy_role or "user"), "User"
            )
            bind.execute(
                sa.text(
                    "INSERT INTO user_roles (id, user_id, role_id, source) "
                    "VALUES (:id, :uid, :rid, 'manual')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "uid": str(user_id),
                    "rid": name_to_role_id[system_name],
                },
            )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_table("roles")
```

- [ ] **Step 2: Run the migration against the test DB**

Run: `cd backend && py -m alembic upgrade head`
Expected: completes without error; `\d roles` and `\d user_roles` exist.

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/versions/068_rbac_roles.py
git commit -m "feat: migration for RBAC roles + system role seed/backfill"
```

---

### Task 4: Capability resolver service

**Files:**
- Create: `backend/app/services/rbac_service.py`
- Test: `backend/tests/test_rbac_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rbac_service.py
import uuid

import pytest

from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services.rbac_service import get_user_capabilities, seed_system_roles


@pytest.mark.asyncio
async def test_capabilities_union_across_roles(db_session, test_tenant):
    user = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
                email="u@testfirm.com", role="user")
    db_session.add(user)
    r1 = Role(tenant_id=test_tenant.id, name="A", capabilities=["manage_matters"])
    r2 = Role(tenant_id=test_tenant.id, name="B",
              capabilities=["manage_matters", "view_billing"])
    db_session.add_all([r1, r2])
    await db_session.flush()
    db_session.add_all([
        UserRole(user_id=user.id, role_id=r1.id, source="manual"),
        UserRole(user_id=user.id, role_id=r2.id, source="group_sync"),
    ])
    await db_session.commit()

    caps = await get_user_capabilities(db_session, user.id)
    assert caps == {"manage_matters", "view_billing"}


@pytest.mark.asyncio
async def test_seed_system_roles_idempotent(db_session, test_tenant):
    await seed_system_roles(db_session, test_tenant.id)
    await seed_system_roles(db_session, test_tenant.id)  # second call no-ops
    from sqlalchemy import select, func
    count = await db_session.scalar(
        select(func.count()).select_from(Role).where(
            Role.tenant_id == test_tenant.id, Role.is_system.is_(True)
        )
    )
    assert count == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_rbac_service.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.rbac_service`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/rbac_service.py
"""Role/capability resolution and system-role seeding."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Role, UserRole
from app.services.capabilities import SYSTEM_ROLE_CAPABILITIES


async def get_user_capabilities(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = (
        await db.execute(
            select(Role.capabilities)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    ).all()
    caps: set[str] = set()
    for (capabilities,) in rows:
        caps.update(capabilities or [])
    return caps


async def seed_system_roles(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the four system roles for a tenant if absent. Idempotent."""
    existing = set(
        (
            await db.execute(
                select(Role.name).where(
                    Role.tenant_id == tenant_id, Role.is_system.is_(True)
                )
            )
        ).scalars()
    )
    for name, caps in SYSTEM_ROLE_CAPABILITIES.items():
        if name in existing:
            continue
        db.add(
            Role(
                tenant_id=tenant_id,
                name=name,
                description=f"System role: {name}",
                capabilities=list(caps),
                is_system=True,
            )
        )
    await db.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_rbac_service.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rbac_service.py backend/tests/test_rbac_service.py
git commit -m "feat: capability resolver + system role seeding"
```

---

### Task 5: `require_capability` dependency + migrate `require_admin`/`require_finance_admin`

**Files:**
- Modify: `backend/app/services/access_control.py`
- Modify: `backend/app/middleware/tenant.py:158-167` (`require_admin`)
- Test: `backend/tests/test_rbac_enforcement.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rbac_enforcement.py
import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import get_db
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services.access_control import require_capability

settings = get_settings()


def _token(user):
    payload = {
        "sub": str(user.id), "tenant_id": str(user.tenant_id),
        "role": user.role, "email": user.email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest.mark.asyncio
async def test_require_capability_allows_and_denies(db_session, test_tenant):
    app = FastAPI()

    @app.get("/needs-billing")
    async def needs_billing(user=Depends(require_capability("view_billing"))):
        return {"ok": True}

    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db

    allowed = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
                   email="acct@testfirm.com", role="user")
    denied = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
                  email="plain@testfirm.com", role="user")
    db_session.add_all([allowed, denied])
    role = Role(tenant_id=test_tenant.id, name="Biller",
                capabilities=["view_billing"])
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRole(user_id=allowed.id, role_id=role.id, source="manual"))
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r_allow = await c.get("/needs-billing",
                              headers={"Authorization": f"Bearer {_token(allowed)}"})
        r_deny = await c.get("/needs-billing",
                             headers={"Authorization": f"Bearer {_token(denied)}"})

    assert r_allow.status_code == 200
    assert r_deny.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_rbac_enforcement.py -v`
Expected: FAIL with `ImportError: cannot import name 'require_capability'`

- [ ] **Step 3: Implement `require_capability` and migrate finance/admin deps**

Append to `backend/app/services/access_control.py`:

```python
from app.services.rbac_service import get_user_capabilities


def require_capability(capability: str):
    """Dependency factory: 403 unless the user holds `capability` via any role."""

    async def _dep(request: Request, db: AsyncSession = Depends(get_db)):
        user = await get_current_user(request, db)
        caps = await get_user_capabilities(db, user.id)
        if capability not in caps:
            raise HTTPException(
                status_code=403, detail=f"Missing capability: {capability}"
            )
        return user

    return _dep
```

Replace the body of `require_finance_admin` (same file) so it consults capabilities, keeping the legacy column as a fallback so nothing regresses mid-migration:

```python
async def require_finance_admin(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Allow tenant admins and accountants into billing/licensing surfaces."""
    user = await get_current_user(request, db)
    caps = await get_user_capabilities(db, user.id)
    if "view_billing" in caps or "manage_billing" in caps:
        return user
    if can_manage_finance(user.role):  # legacy fallback
        return user
    raise HTTPException(status_code=403, detail="Finance access required")
```

In `backend/app/middleware/tenant.py`, change `require_admin` to check the capability with a legacy fallback:

```python
async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """FastAPI dependency that enforces admin access (admin_settings capability)."""
    from app.services.rbac_service import get_user_capabilities

    user = await get_current_user(request, db)
    caps = await get_user_capabilities(db, user.id)
    if "admin_settings" in caps or user.role == "admin":  # legacy fallback
        return user
    raise HTTPException(status_code=403, detail="Admin access required")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_rbac_enforcement.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the existing admin/licensing suites to confirm no regression**

Run: `cd backend && py -m pytest tests/test_licensing_access.py tests/test_module_guard.py -q`
Expected: PASS (all previously-passing tests still pass; seeded users with legacy `role="admin"` still get in via fallback).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/access_control.py backend/app/middleware/tenant.py backend/tests/test_rbac_enforcement.py
git commit -m "feat: require_capability dependency; migrate admin/finance gates to capabilities"
```

---

### Task 6: Last-admin guard

**Files:**
- Modify: `backend/app/services/rbac_service.py`
- Test: `backend/tests/test_rbac_service.py` (add to existing file)

- [ ] **Step 1: Write the failing test (append)**

```python
@pytest.mark.asyncio
async def test_count_admin_capable_users(db_session, test_tenant):
    from app.services.rbac_service import count_admin_capable_users
    admin_role = Role(tenant_id=test_tenant.id, name="Admins",
                      capabilities=["admin_settings"])
    db_session.add(admin_role)
    u = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
             email="a@testfirm.com", role="user")
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserRole(user_id=u.id, role_id=admin_role.id, source="manual"))
    await db_session.commit()

    assert await count_admin_capable_users(db_session, test_tenant.id) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_rbac_service.py::test_count_admin_capable_users -v`
Expected: FAIL with `ImportError: cannot import name 'count_admin_capable_users'`

- [ ] **Step 3: Implement (append to `rbac_service.py`)**

```python
async def count_admin_capable_users(
    db: AsyncSession, tenant_id: uuid.UUID
) -> int:
    """Number of distinct active users in the tenant holding admin_settings."""
    from app.models.user import User

    rows = (
        await db.execute(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                Role.tenant_id == tenant_id,
                User.is_active.is_(True),
                Role.capabilities.contains(["admin_settings"]),
            )
            .distinct()
        )
    ).all()
    return len(rows)
```

> Note: `JSONB.contains(["admin_settings"])` emits the Postgres `@>` containment operator — true when the array holds that element.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_rbac_service.py::test_count_admin_capable_users -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rbac_service.py backend/tests/test_rbac_service.py
git commit -m "feat: count_admin_capable_users for last-admin guard"
```

---

### Task 7: Roles admin API (CRUD + assign to users)

**Files:**
- Create: `backend/app/routers/roles.py`
- Modify: `backend/app/main.py` (register router; mirror existing `include_router` lines)
- Test: `backend/tests/test_roles_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_roles_api.py
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.rbac import Role, UserRole
from app.models.user import User

settings = get_settings()


async def _admin_client(db_session, test_tenant):
    admin = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
                 email="admin@testfirm.com", role="user")
    db_session.add(admin)
    role = Role(tenant_id=test_tenant.id, name="Admins",
                capabilities=["admin_settings", "manage_roles"])
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRole(user_id=admin.id, role_id=role.id, source="manual"))
    await db_session.commit()
    payload = {"sub": str(admin.id), "tenant_id": str(admin.tenant_id),
               "role": "user", "email": admin.email,
               "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    token = jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"}), admin


@pytest.mark.asyncio
async def test_create_and_list_role(db_session, test_tenant):
    client, _ = await _admin_client(db_session, test_tenant)
    async with client:
        created = await client.post("/api/admin/roles", json={
            "name": "Paralegal", "description": "Paralegal staff",
            "capabilities": ["manage_matters", "manage_documents"]})
        listed = await client.get("/api/admin/roles")
    app.dependency_overrides.clear()
    assert created.status_code == 201
    assert created.json()["name"] == "Paralegal"
    names = [r["name"] for r in listed.json()]
    assert "Paralegal" in names


@pytest.mark.asyncio
async def test_create_role_rejects_unknown_capability(db_session, test_tenant):
    client, _ = await _admin_client(db_session, test_tenant)
    async with client:
        resp = await client.post("/api/admin/roles", json={
            "name": "Bad", "capabilities": ["not_real"]})
    app.dependency_overrides.clear()
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_roles_api.py -v`
Expected: FAIL (404 — router not registered)

- [ ] **Step 3: Implement the router**

```python
# backend/app/routers/roles.py
"""Tenant role registry CRUD + per-user role assignment. manage_roles gated."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.tenant import get_current_user
from app.models.rbac import Role, UserRole
from app.services.access_control import require_capability
from app.services.capabilities import CAPABILITIES, is_valid_capability
from app.services.rbac_service import count_admin_capable_users

router = APIRouter(prefix="/api/admin/roles", tags=["roles"])


class RoleIn(BaseModel):
    name: str
    description: str | None = None
    capabilities: list[str] = []

    @field_validator("capabilities")
    @classmethod
    def _valid_caps(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if not is_valid_capability(c)]
        if bad:
            raise ValueError(f"Unknown capabilities: {bad}")
        return v


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    capabilities: list[str]
    is_system: bool


class AssignIn(BaseModel):
    role_ids: list[uuid.UUID]


@router.get("", response_model=list[RoleOut])
async def list_roles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    rows = (
        await db.execute(
            select(Role).where(Role.tenant_id == user.tenant_id).order_by(Role.name)
        )
    ).scalars().all()
    return rows


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = Role(
        tenant_id=user.tenant_id,
        name=body.name,
        description=body.description,
        capabilities=body.capabilities,
        is_system=False,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    role.name = body.name
    role.description = body.description
    role.capabilities = body.capabilities
    await db.commit()
    await db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    await db.delete(role)
    await db.commit()


@router.put("/assign/{target_user_id}", status_code=200)
async def assign_roles(
    target_user_id: uuid.UUID,
    body: AssignIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    # Replace the user's MANUAL assignments only; group_sync rows are untouched.
    existing = (
        await db.execute(
            select(UserRole).where(
                UserRole.user_id == target_user_id, UserRole.source == "manual"
            )
        )
    ).scalars().all()
    for ur in existing:
        await db.delete(ur)
    await db.flush()

    valid_role_ids = set(
        (
            await db.execute(
                select(Role.id).where(
                    Role.tenant_id == user.tenant_id, Role.id.in_(body.role_ids)
                )
            )
        ).scalars()
    )
    for rid in valid_role_ids:
        db.add(UserRole(user_id=target_user_id, role_id=rid, source="manual"))
    await db.flush()

    # Last-admin guard: never let an assignment leave the tenant with zero admins.
    if await count_admin_capable_users(db, user.tenant_id) == 0:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="This change would remove the last admin in the firm.",
        )
    await db.commit()
    return {"assigned": [str(r) for r in valid_role_ids]}
```

Register in `backend/app/main.py` (follow the existing import + `include_router` pattern):

```python
from app.routers.roles import router as roles_router
# ...
app.include_router(roles_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_roles_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/roles.py backend/app/main.py backend/tests/test_roles_api.py
git commit -m "feat: roles admin API (CRUD + assignment) with last-admin guard"
```

---

### Task 8: Add `caps` to the login JWT and `/me`

**Files:**
- Modify: `backend/app/routers/auth.py` (`_create_access_token`, `_issue_access_token`)
- Test: `backend/tests/test_rbac_jwt.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rbac_jwt.py
import uuid

import pytest
from jose import jwt as jose_jwt

from app.config import get_settings
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.models.tenant import Tenant
from app.routers.auth import _issue_access_token

settings = get_settings()


@pytest.mark.asyncio
async def test_issued_token_carries_caps(db_session, test_tenant):
    user = User(id=uuid.uuid4(), tenant_id=test_tenant.id,
                email="capuser@testfirm.com", role="user")
    db_session.add(user)
    role = Role(tenant_id=test_tenant.id, name="Matters",
                capabilities=["manage_matters"])
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=role.id, source="manual"))
    await db_session.commit()

    tenant = await db_session.get(Tenant, test_tenant.id)
    token = await _issue_access_token(db_session, user, tenant)
    payload = jose_jwt.decode(token, settings.SECRET_KEY,
                              algorithms=[settings.ALGORITHM])
    assert "manage_matters" in payload["caps"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && py -m pytest tests/test_rbac_jwt.py -v`
Expected: FAIL with `KeyError: 'caps'`

- [ ] **Step 3: Implement**

Change `_create_access_token` to accept and embed caps, and have `_issue_access_token` resolve them. In `backend/app/routers/auth.py`:

```python
def _create_access_token(
    user: User, tenant: Tenant, plan_id: str = "full-platform",
    caps: list[str] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "caps": caps or [],
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "plan": plan_id,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _issue_access_token(db: AsyncSession, user: User, tenant: Tenant) -> str:
    """Resolve the tenant's plan + user capabilities and mint an access token."""
    from app.services.module_visibility import resolve_plan_meta
    from app.services.rbac_service import get_user_capabilities

    plan_id, _ = await resolve_plan_meta(db, user.tenant_id)
    caps = sorted(await get_user_capabilities(db, user.id))
    return _create_access_token(user, tenant, plan_id, caps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && py -m pytest tests/test_rbac_jwt.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && py -m pytest -q`
Expected: PASS (no regressions). Investigate and fix any failure before continuing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/auth.py backend/tests/test_rbac_jwt.py
git commit -m "feat: embed effective capabilities in access token"
```

---

### Task 9: Frontend — Roles tab + role assignment

**Files:**
- Modify: `frontend/src/api.js` (add role API calls)
- Create: `frontend/src/pages/admin/RolesTab.jsx`
- Modify: `frontend/src/pages/AdminPage.jsx` (add "Roles" tab; replace the 3-way cycle toggle at lines ~427-543 with a role multi-select that calls `assignUserRoles`)

> Frontend has no unit-test harness here; verification is manual via the running app.

- [ ] **Step 1: Add API calls to `frontend/src/api.js`**

```javascript
export const listRoles = () => api.get('/admin/roles').then((r) => r.data)
export const createRole = (body) => api.post('/admin/roles', body).then((r) => r.data)
export const updateRole = (id, body) =>
  api.put(`/admin/roles/${id}`, body).then((r) => r.data)
export const deleteRole = (id) => api.delete(`/admin/roles/${id}`).then((r) => r.data)
export const assignUserRoles = (userId, roleIds) =>
  api.put(`/admin/roles/assign/${userId}`, { role_ids: roleIds }).then((r) => r.data)
```

- [ ] **Step 2: Create `frontend/src/pages/admin/RolesTab.jsx`**

```jsx
import { useEffect, useState } from 'react'
import { listRoles, createRole, deleteRole } from '../../api'

const CAPABILITIES = [
  'manage_users', 'manage_roles', 'manage_billing', 'view_billing',
  'manage_matters', 'manage_intake', 'manage_documents',
  'manage_integrations', 'admin_settings', 'use_premium_ai',
]

export default function RolesTab() {
  const [roles, setRoles] = useState([])
  const [name, setName] = useState('')
  const [caps, setCaps] = useState([])
  const [error, setError] = useState('')

  const load = () => listRoles().then(setRoles).catch(() => setError('Failed to load roles'))
  useEffect(() => { load() }, [])

  const toggleCap = (c) =>
    setCaps((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      await createRole({ name: name.trim(), capabilities: caps })
      setName(''); setCaps([]); load()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to create role')
    }
  }

  return (
    <div className="space-y-6">
      {error && <div className="text-red-600 text-sm">{error}</div>}
      <form onSubmit={submit} className="space-y-3">
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Role name (e.g. Paralegal)" className="border px-3 py-2 rounded w-full" />
        <div className="grid grid-cols-2 gap-2">
          {CAPABILITIES.map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={caps.includes(c)} onChange={() => toggleCap(c)} />
              {c}
            </label>
          ))}
        </div>
        <button type="submit" className="bg-brand-ink text-white px-4 py-2 rounded">
          Create role
        </button>
      </form>
      <table className="w-full text-sm">
        <thead><tr><th className="text-left">Role</th><th className="text-left">Capabilities</th><th /></tr></thead>
        <tbody>
          {roles.map((r) => (
            <tr key={r.id} className="border-t">
              <td className="py-2">{r.name}{r.is_system && ' (system)'}</td>
              <td>{(r.capabilities || []).join(', ')}</td>
              <td className="text-right">
                {!r.is_system && (
                  <button onClick={() => deleteRole(r.id).then(load)} className="text-red-600">Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: Wire the tab into `AdminPage.jsx`**

Add `RolesTab` to the admin tab set (mirror how existing tabs are registered around `ADMIN_TABS` / `tabs` at `AdminPage.jsx:1103`). Add a `{ key: 'roles', label: 'Roles' }` entry and render `<RolesTab />` when active. Replace the per-user 3-way role cycle button (`handleRoleToggle`, ~`AdminPage.jsx:427-543`) with a multi-select of roles that calls `assignUserRoles(u.id, selectedRoleIds)`; keep the existing OAuth-grantor confirmation guard.

- [ ] **Step 4: Manual verification**

Run the frontend (`cd frontend && npm run dev`) + backend, log in as an admin, open Admin → Roles. Create a "Paralegal" role with `manage_matters` + `manage_documents`. Confirm it lists, assign it to a user, and confirm the user's `/me` reflects the capabilities.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/pages/admin/RolesTab.jsx frontend/src/pages/AdminPage.jsx
git commit -m "feat: admin Roles tab + per-user role assignment"
```

---

### Task 10: Seed system roles for newly-created tenants

**Files:**
- Modify: the tenant-creation path (search for where `Tenant(...)` is constructed and committed during registration/onboarding — likely `backend/app/routers/auth.py` registration or an onboarding service)
- Test: `backend/tests/test_rbac_service.py` (reuse `seed_system_roles`, already covered)

- [ ] **Step 1: Locate tenant creation**

Run: `grep -rn "Tenant(" backend/app/routers backend/app/services` (use the Grep tool) and identify where a new tenant is persisted during signup/onboarding.

- [ ] **Step 2: Call `seed_system_roles` after tenant insert**

After the new tenant is flushed/committed, call:

```python
from app.services.rbac_service import seed_system_roles
await seed_system_roles(db, tenant.id)
```

Then assign the creating user the `Administrator` system role via a `UserRole(source="manual")` row (look up the seeded role by `name == "Administrator"` and `tenant_id`).

- [ ] **Step 3: Verify**

Run: `cd backend && py -m pytest tests/test_rbac_service.py -q`
Expected: PASS. Manually register a new firm and confirm 4 system roles exist and the creator is Administrator.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/auth.py
git commit -m "feat: seed system roles on tenant creation"
```

---

## Self-Review Notes

- **Spec coverage:** roles registry (Task 2/3), capabilities catalog (Task 1), user_roles + union (Task 4), migration + seed/backfill (Task 3, Task 10 for new tenants), enforcement via capabilities (Task 5), last-admin guard (Task 6, enforced in Task 7 assignment), JWT caps claim (Task 8), admin UI (Task 9). `module_guard`/tenant-plan modules intentionally untouched. Inline `user.role` checks intentionally deferred (documented in the compatibility boundary).
- **Phase 2 (M365 sync)** is a separate plan, written after this lands.
- **Risk note:** the migration's per-tenant seed/backfill assumes migrations run as the table owner (RLS bypass per migration 044). If a tenant already has a role named "Administrator", the unique constraint will abort the migration — acceptable for current data (no custom roles exist yet).

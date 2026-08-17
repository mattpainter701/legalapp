from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.tenant_state import require_active_tenant


def test_tenant_without_expiry_remains_active():
    tenant = SimpleNamespace(is_active=True, expires_at=None)
    assert require_active_tenant(tenant) is tenant


def test_future_tenant_expiry_remains_active():
    tenant = SimpleNamespace(
        is_active=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert require_active_tenant(tenant) is tenant


def test_expired_tenant_fails_closed():
    tenant = SimpleNamespace(
        is_active=True,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(HTTPException, match="expired") as exc:
        require_active_tenant(tenant)
    assert exc.value.status_code == 403


def test_naive_expiry_is_interpreted_as_utc():
    tenant = SimpleNamespace(
        is_active=True,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(seconds=1),
    )
    with pytest.raises(HTTPException, match="expired"):
        require_active_tenant(tenant)

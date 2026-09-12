"""Authorization and resource limits that do not require a live database."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from unittest.mock import AsyncMock

from app.routers.plugins import _assert_addon_runnable, _entitlement_status
from app.schemas.mediation import PortalCaseSummary
from app.services import mediation_service as ms
from app.middleware import addon_guard
from app.services.plugins.executor import PluginExecutor, has_specialised_prompt


@pytest.mark.parametrize("status", ["trial", "purchased", "included"])
def test_future_entitlements_are_not_runnable(status):
    row = SimpleNamespace(
        status=status,
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        expires_at=None,
    )
    assert _entitlement_status(row) == "scheduled"
    with pytest.raises(HTTPException) as exc:
        _assert_addon_runnable("commercial-legal", row)
    assert exc.value.status_code == 402


def test_trial_without_expiry_is_not_runnable_and_disabled_stays_disabled():
    row = SimpleNamespace(status="trial", starts_at=None, expires_at=None)
    assert _entitlement_status(row) == "expired"
    with pytest.raises(HTTPException) as exc:
        _assert_addon_runnable("privacy-legal", row)
    assert exc.value.status_code == 402
    row.status = "disabled"
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert _entitlement_status(row) == "disabled"


def test_case_summary_cannot_serialize_internal_strategy_or_identifiers():
    summary = PortalCaseSummary(
        id=str(uuid4()),
        case_name="Public case",
        status="active",
        summary="Private caucus strategy",
        fixed_fee="5000",
        waiting_on="Attorney advice",
        matter_id=str(uuid4()),
        client_contact_id=str(uuid4()),
        assets_count=5,
    )
    assert (
        not {
            "summary",
            "fixed_fee",
            "waiting_on",
            "matter_id",
            "client_contact_id",
            "assets_count",
        }
        & summary.model_dump().keys()
    )


@pytest.mark.parametrize(
    "status",
    [
        "draft",
        "submitted",
        "attorney_approved",
        "sent",
        "opposing_approved",
        "disputed",
    ],
)
def test_asset_requires_recipient_evidence_not_just_sent_status(status):
    owner, recipient, outsider = uuid4(), uuid4(), uuid4()
    asset = SimpleNamespace(
        submitted_by_party_id=owner, released_to_party_id=recipient, status=status
    )
    assert ms.asset_visible_to_party(asset, owner)
    assert not ms.asset_visible_to_party(asset, outsider)
    assert ms.asset_visible_to_party(asset, recipient) == (
        status in ms.SHARED_ASSET_STATUSES
    )
    asset.released_to_party_id = None
    assert not ms.asset_visible_to_party(asset, recipient)


@pytest.mark.asyncio
async def test_mediation_upload_bounds_reads_and_does_not_use_client_filename(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ms.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(ms.settings, "MAX_FILE_SIZE_MB", 1)
    file = UploadFile(filename="../../secret:stream", file=BytesIO(b"reviewed bytes"))
    path, size, digest = await ms.save_case_upload(file, uuid4(), str(uuid4()), uuid4())
    from pathlib import Path

    stored = Path(path)
    assert stored.name == "content"
    assert stored.is_relative_to(tmp_path)
    assert stored.read_bytes() == b"reviewed bytes"
    assert size == 14
    assert digest == ms.hashlib.sha256(b"reviewed bytes").hexdigest()

    class OversizedUpload:
        filename = "too-big.bin"

        async def read(self, size):
            assert size == 1024 * 1024 + 1
            return b"x" * size

    with pytest.raises(HTTPException) as exc:
        await ms.save_case_upload(OversizedUpload(), uuid4(), str(uuid4()), uuid4())
    assert exc.value.status_code == 413


@pytest.mark.parametrize(
    "skill",
    ["mediation-intake", "mediation-brief", "settlement-agreement", "caucus-summary"],
)
def test_mediation_workflows_use_formattable_specialized_templates(skill):
    assert has_specialised_prompt("mediation-legal", skill)
    prompt = PluginExecutor(None).build_system_prompt(
        "mediation-legal", skill, "Firm profile", {"matter_context": "Case records"}
    )
    assert "Firm profile" in prompt and "Case records" in prompt
    assert "ERROR:" not in prompt
    assert "MEDIATION WORKING DRAFT" in prompt
    assert "ROLE AND AUDIENCE" in prompt
    assert "Inclusion in context is not permission to disclose" in prompt
    assert "TASK:" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,strict,expected",
    [
        (None, False, None),
        ("available", False, None),
        (None, True, 402),
        ("purchased", True, None),
        ("disabled", False, 403),
        ("locked", True, 403),
        ("trial", False, 402),
        ("unknown", False, 402),
    ],
)
async def test_specialized_crud_checks_live_entitlements(
    monkeypatch, status, strict, expected
):
    row = (
        SimpleNamespace(status=status, starts_at=None, expires_at=None)
        if status
        else None
    )
    monkeypatch.setattr(
        addon_guard,
        "get_current_user",
        AsyncMock(return_value=SimpleNamespace(tenant_id=uuid4())),
    )
    monkeypatch.setattr(addon_guard, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        addon_guard, "load_plugin_entitlement", AsyncMock(return_value=row)
    )
    monkeypatch.setattr(addon_guard.get_settings(), "PLUGIN_ENTITLEMENT_STRICT", strict)
    dependency = addon_guard.require_addon_workflow("family-law")
    if expected:
        with pytest.raises(HTTPException) as exc:
            await dependency(None, None)
        assert exc.value.status_code == expected
    else:
        await dependency(None, None)

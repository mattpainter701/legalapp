import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.services.search_identity_ticket import (
    SearchIdentity,
    SearchIdentityTicketError,
    mint_search_identity_ticket,
)
from app.services.smb import _path_is_within_binding
from app.schemas.smb import NativeIdentityUpdate
from app.services.native_authorization import (
    NativeAuthorizationError,
    expand_effective_group_sids,
    resolve_native_identity,
)


def test_ticket_contains_only_server_resolved_identity_scope():
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    ticket = mint_search_identity_ticket(
        SearchIdentity(
            tenant_id="tenant-1",
            user_id="user-1",
            source_ids=("share-1",),
            principal_sids=("S-1-5-21-100",),
            identity_version=9,
        ),
        private_key=base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        audience="agent-1",
        filters={"matter_id": "matter-1"},
        now=1_000,
    )
    payload = json.loads(base64.urlsafe_b64decode(ticket.split(".")[1] + "=="))
    assert payload["tenant_id"] == "tenant-1"
    assert payload["source_ids"] == ["share-1"]
    assert payload["filters"] == {"matter_id": "matter-1"}
    assert "role" not in payload and "browser_scope" not in payload


def test_ticket_rejects_oversized_principal_sets():
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    with pytest.raises(SearchIdentityTicketError, match="too large"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="tenant-1",
                user_id="user-1",
                source_ids=("share-1",),
                principal_sids=tuple(f"S-1-5-21-{value}" for value in range(4097)),
                identity_version=1,
            ),
            private_key=base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            audience="agent-1",
        )


def test_file_path_must_be_within_exact_matter_binding():
    share = r"\\FS01\Legal"
    assert _path_is_within_binding(
        r"\\FS01\Legal\Client-1\brief.pdf", share, "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\FS01\Legal\Client-10\brief.pdf", share, "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\FS01\Other\Client-1\brief.pdf", share, "Client-1"
    )


def test_nested_group_sid_expansion_is_cycle_safe_and_complete():
    expanded = expand_effective_group_sids(
        "S-1-5-21-100",
        ["S-1-5-21-200"],
        {
            "S-1-5-21-200": ["S-1-5-21-300"],
            "S-1-5-21-300": ["S-1-5-21-200"],
        },
    )
    assert {"S-1-5-21-100", "S-1-5-21-200", "S-1-5-21-300"}.issubset(expanded)


def test_partial_or_oversized_group_expansion_fails_closed():
    with pytest.raises(NativeAuthorizationError):
        expand_effective_group_sids("S-1-5-21-100", ["not-a-sid"], {})
    with pytest.raises(NativeAuthorizationError):
        expand_effective_group_sids(
            "S-1-5-21-100", ["S-1-5-21-200"], {}, max_principals=1
        )


@pytest.mark.asyncio
async def test_stale_or_malformed_stored_identity_fails_closed():
    now = datetime.now(timezone.utc)

    class FakeDb:
        def __init__(self, row):
            self.row = row

        async def scalar(self, _statement):
            return self.row

    base = {
        "state": "healthy",
        "expires_at": now + timedelta(hours=1),
        "resolved_at": now - timedelta(hours=1),
        "primary_sid": "S-1-5-21-100",
        "effective_sids": ["S-1-5-21-200"],
        "version": 1,
        "provider": "ad",
    }
    with pytest.raises(NativeAuthorizationError, match="stale"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**base)),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )
    malformed = {**base, "resolved_at": now, "effective_sids": ["not-a-sid"]}
    with pytest.raises(NativeAuthorizationError, match="SID set"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**malformed)),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )


def test_native_identity_resolution_timestamps_require_timezone():
    with pytest.raises(ValueError, match="timezone"):
        NativeIdentityUpdate(
            provider="ad",
            directory_tenant_id="directory-1",
            object_id="object-1",
            primary_sid="S-1-5-21-100",
            effective_sids=["S-1-5-21-200"],
            group_expansion_complete=True,
            state="healthy",
            resolved_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 2),
        )

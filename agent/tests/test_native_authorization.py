import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clarity_agent.native_acl import authorize_acl, capture_smb_acl, normalize_sddl
from clarity_agent.search_identity import (
    IdentityTicketError,
    ReplayCache,
    verify_search_identity_ticket,
)
from clarity_agent.task_worker import TaskWorker


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _ticket(private_key, *, now=1_000, **overrides):
    header = {"alg": "EdDSA", "kid": "firm-memory-v1", "typ": "JWT"}
    payload = {
        "v": 1,
        "iss": "lawhand-saas",
        "aud": "agent-1",
        "sub": "user-1",
        "tenant_id": "tenant-1",
        "source_ids": ["share-1"],
        "principal_sids": ["S-1-5-21-100", "S-1-5-21-200", "S-1-5-11"],
        "identity_version": 4,
        "filters": {"matter_id": "matter-1"},
        "nonce": "nonce-1",
        "iat": now,
        "exp": now + 60,
    }
    payload.update(overrides)
    first = _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    second = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signed = f"{first}.{second}".encode()
    return f"{signed.decode()}.{_b64(private_key.sign(signed))}"


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, _b64(public)


def _verify(ticket, public_key, cache=None, **kwargs):
    return verify_search_identity_ticket(
        ticket,
        public_key=public_key,
        audience=kwargs.pop("audience", "agent-1"),
        tenant_id=kwargs.pop("tenant_id", "tenant-1"),
        required_source_ids=kwargs.pop("sources", {"share-1"}),
        replay_cache=cache or ReplayCache(),
        now=kwargs.pop("now", 1_000),
    )


def test_ticket_accepts_nested_group_sid_and_is_source_bound(keys):
    private, public = keys
    auth = _verify(_ticket(private), public)
    assert "S-1-5-21-200" in auth.principal_sids
    assert auth.filters == {"matter_id": "matter-1"}


@pytest.mark.parametrize(
    ("change", "kwargs"),
    [
        ({"tenant_id": "other"}, {}),
        ({"aud": "other-agent"}, {}),
        ({"source_ids": ["other-share"]}, {}),
        ({"exp": 999}, {}),
        ({"iat": 900, "exp": 1_301}, {}),
    ],
)
def test_ticket_identity_scope_and_lifetime_mismatch_fail_closed(keys, change, kwargs):
    private, public = keys
    with pytest.raises(IdentityTicketError):
        _verify(_ticket(private, **change), public, **kwargs)


def test_forged_and_replayed_tickets_fail_closed(keys):
    private, public = keys
    attacker = Ed25519PrivateKey.generate()
    with pytest.raises(IdentityTicketError, match="signature"):
        _verify(_ticket(attacker), public)
    cache = ReplayCache()
    ticket = _ticket(private)
    _verify(ticket, public, cache=cache)
    with pytest.raises(IdentityTicketError, match="replayed"):
        _verify(ticket, public, cache=cache)


def test_oversized_principal_set_fails_closed(keys):
    private, public = keys
    principals = [f"S-1-5-21-{value}" for value in range(4097)]
    with pytest.raises(IdentityTicketError, match="principal set"):
        _verify(_ticket(private, principal_sids=principals), public)


def test_task_filters_must_match_signed_ticket(keys):
    private, public = keys
    worker = TaskWorker.__new__(TaskWorker)
    worker.config = SimpleNamespace(
        # A signed task must never fall back to the legacy untrimmed path even
        # when the local rollout switch has not yet been enabled.
        native_authz_enabled=False,
        search_identity_public_key=public,
        agent_id="agent-1",
    )
    worker._ticket_replays = ReplayCache()
    with pytest.raises(IdentityTicketError, match="filter scope"):
        worker._authorization_for_task(
            {
                "kind": "authorize_file",
                "tenant_id": "tenant-1",
                "matter_id": "matter-2",
                "identity_ticket": _ticket(private, now=int(time.time())),
            },
            {"share-1"},
        )


def _search_worker(public):
    worker = TaskWorker.__new__(TaskWorker)
    worker.config = SimpleNamespace(
        native_authz_enabled=False,
        search_identity_public_key=public,
        agent_id="agent-1",
    )
    worker._ticket_replays = ReplayCache()
    return worker


def test_firm_wide_task_matter_set_must_match_the_signed_ticket(keys):
    """A ticket for one matter set must not authorize a task for another."""
    private, public = keys
    now = int(time.time())
    task = {
        "kind": "local_search",
        "tenant_id": "tenant-1",
        "matter_ids": ["matter-1", "matter-9"],
        "file_extensions": [],
        "identity_ticket": _ticket(
            private,
            now=now,
            filters={"matter_ids": ["matter-1", "matter-2"], "file_extensions": []},
        ),
    }
    with pytest.raises(IdentityTicketError, match="filter scope"):
        _search_worker(public)._authorization_for_task(task, {"share-1"})


def test_firm_wide_task_accepts_its_own_matter_set_in_any_order(keys):
    private, public = keys
    now = int(time.time())
    task = {
        "kind": "local_search",
        "tenant_id": "tenant-1",
        "matter_ids": ["matter-9", "matter-1", "matter-9"],
        "file_extensions": [".PDF"],
        "identity_ticket": _ticket(
            private,
            now=now,
            filters={
                "matter_ids": ["matter-1", "matter-9"],
                "file_extensions": ["pdf"],
            },
        ),
    }
    authorization = _search_worker(public)._authorization_for_task(task, {"share-1"})
    assert authorization.filters["matter_ids"] == ["matter-1", "matter-9"]


def test_a_task_without_a_matter_set_keeps_the_single_matter_binding(keys):
    """An older single-matter task must behave exactly as it always has."""
    private, public = keys
    now = int(time.time())
    worker = _search_worker(public)
    task = {
        "kind": "authorize_file",
        "tenant_id": "tenant-1",
        "matter_id": "matter-1",
        "identity_ticket": _ticket(private, now=now),
    }
    assert worker._authorization_for_task(task, {"share-1"}).filters == {
        "matter_id": "matter-1"
    }


def test_explicit_deny_wins_over_allow_and_inheritance():
    record = normalize_sddl(
        "O:S-1-5-18G:S-1-5-18D:(A;ID;FR;;;S-1-5-21-200)(D;;FR;;;S-1-5-21-100)",
        captured_at=1_000,
    )
    decision = authorize_acl(
        record, {"S-1-5-21-100", "S-1-5-21-200"}, max_age_seconds=300, now=1_010
    )
    assert decision.allowed is False
    assert decision.reason == "acl_explicit_deny"


def test_unknown_and_stale_acl_fail_closed_but_fresh_group_allow_passes():
    assert not authorize_acl(
        None, {"S-1-5-21-200"}, max_age_seconds=60, now=1_000
    ).allowed
    record = normalize_sddl("D:(A;;FR;;;S-1-5-21-200)", captured_at=900)
    assert (
        authorize_acl(record, {"S-1-5-21-200"}, max_age_seconds=60, now=1_000).reason
        == "acl_stale"
    )
    assert authorize_acl(
        record, {"S-1-5-21-200"}, max_age_seconds=200, now=1_000
    ).allowed


def test_authenticated_smb_acl_capture_uses_supplied_session_and_ignores_inherit_only(
    monkeypatch,
):
    import smbclient._io as smb_io
    import smbprotocol.security_descriptor as security

    calls = {}

    class Field:
        def __init__(self, value):
            self.value = value

        def get_value(self):
            return self.value

    class FakeAllow:
        def __init__(self, sid, flags=0):
            self.fields = {
                "mask": Field(0x1),
                "sid": Field(sid),
                "ace_flags": Field(flags),
            }

        def __getitem__(self, key):
            return self.fields[key]

    class FakeDeny(FakeAllow):
        pass

    class Descriptor:
        def get_dacl(self):
            return {
                "aces": Field(
                    [
                        FakeAllow("S-1-5-21-100"),
                        FakeAllow("S-1-5-21-999", flags=0x08),
                        FakeDeny("S-1-5-21-200"),
                    ]
                )
            }

    class FakeRaw:
        def __init__(self, path, **kwargs):
            calls.update({"path": path, **kwargs})

    class FakeTransaction:
        def __init__(self, _raw):
            self.results = ()

        def commit(self):
            self.results = (Descriptor(),)

    monkeypatch.setattr(smb_io, "SMBFileIO", FakeRaw)
    monkeypatch.setattr(smb_io, "SMBFileTransaction", FakeTransaction)
    monkeypatch.setattr(smb_io, "query_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(security, "AccessAllowedAce", FakeAllow)
    monkeypatch.setattr(security, "AccessDeniedAce", FakeDeny)

    record = capture_smb_acl(
        r"\\FS01\Legal\brief.pdf", {"username": "svc", "password": "secret"}
    )

    assert calls["username"] == "svc"
    assert record["state"] == "healthy"
    assert record["allow"] == [{"sid": "S-1-5-21-100", "inherited": False}]
    assert record["deny"] == [{"sid": "S-1-5-21-200", "inherited": False}]

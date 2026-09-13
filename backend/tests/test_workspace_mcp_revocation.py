"""Which assistants a security action disconnects, and which are offered back.

``pending_reconnect_clients`` is the only thing standing between a user and
"my assistant just stopped working with no explanation", so its edges matter:
it must not offer a reconnect that the product would refuse, must not nag about
a client that is already back, and must not surface a disconnect the user
performed deliberately.
"""

import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import workspace_mcp_revocation


def _user(*, privacy_mode: bool = False):
    return types.SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), privacy_mode=privacy_mode
    )


def _grant(
    *,
    client_id: str,
    client_name: str,
    reason: str = workspace_mcp_revocation.PASSWORD_RESET_REASON,
    revoked_ago: timedelta = timedelta(minutes=5),
):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        client_id=client_id,
        client_name=client_name,
        revocation_reason=reason,
        revoked_at=datetime.now(timezone.utc) - revoked_ago,
        status="revoked",
    )


class FakeScalars:
    """Returns each queued result in turn, like the two queries the code runs."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def __call__(self, _query):
        self.calls += 1
        result = self._results.pop(0) if self._results else []
        return types.SimpleNamespace(all=lambda: result)


def _db(*results):
    return types.SimpleNamespace(scalars=FakeScalars(results))


@pytest.mark.asyncio
async def test_reset_revoked_assistants_are_offered_back():
    db = _db([_grant(client_id="claude", client_name="Claude")], [])
    pending = await workspace_mcp_revocation.pending_reconnect_clients(db, _user())

    assert [item["client_name"] for item in pending] == ["Claude"]
    assert pending[0]["client_id"] == "claude"
    assert pending[0]["disconnected_at"]


@pytest.mark.asyncio
async def test_reconnecting_is_what_clears_the_prompt():
    """No dismissal bookkeeping: a live grant for the client ends the nudge."""
    db = _db([_grant(client_id="claude", client_name="Claude")], ["claude"])
    assert await workspace_mcp_revocation.pending_reconnect_clients(db, _user()) == []


@pytest.mark.asyncio
async def test_privacy_mode_suppresses_the_prompt_entirely():
    """Inviting a reconnect the product would refuse is worse than silence."""
    db = _db([_grant(client_id="claude", client_name="Claude")], [])
    pending = await workspace_mcp_revocation.pending_reconnect_clients(
        db, _user(privacy_mode=True)
    )

    assert pending == []
    assert db.scalars.calls == 0  # short-circuits before touching the database


@pytest.mark.asyncio
async def test_a_deliberate_disconnect_is_never_offered_back():
    """Privacy Mode and a user's own revoke are choices, not accidents.

    Only the password-reset reason reaches the prompt, which the query filter
    enforces; this pins the constant the filter and the reset path share.
    """
    assert (
        workspace_mcp_revocation.PASSWORD_RESET_REASON
        != workspace_mcp_revocation.PRIVACY_MODE_REASON
    )
    db = _db([], [])
    assert await workspace_mcp_revocation.pending_reconnect_clients(db, _user()) == []


@pytest.mark.asyncio
async def test_one_entry_per_client_across_repeated_resets():
    db = _db(
        [
            _grant(client_id="claude", client_name="Claude"),
            _grant(
                client_id="claude",
                client_name="Claude",
                revoked_ago=timedelta(days=3),
            ),
            _grant(client_id="chatgpt", client_name="ChatGPT"),
        ],
        [],
    )
    pending = await workspace_mcp_revocation.pending_reconnect_clients(db, _user())

    assert [item["client_id"] for item in pending] == ["claude", "chatgpt"]


@pytest.mark.asyncio
async def test_nothing_revoked_means_nothing_to_say():
    db = _db([], [])
    assert await workspace_mcp_revocation.pending_reconnect_clients(db, _user()) == []
    assert db.scalars.calls == 1  # the second query is skipped when the first is empty


@pytest.mark.asyncio
async def test_runtime_cleanup_survives_an_unreachable_cache(monkeypatch):
    """The committed grant row is authoritative; Redis cleanup is best effort."""
    calls = []

    async def boom(_request, grant_id):
        calls.append(grant_id)
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.services.workspace_mcp_oauth.revoke_workspace_grant_runtime", boom
    )
    grants = [
        _grant(client_id="claude", client_name="Claude"),
        _grant(client_id="chatgpt", client_name="ChatGPT"),
    ]

    await workspace_mcp_revocation.cleanup_revoked_grant_runtime(object(), grants)

    # Every grant is attempted; one failure does not abandon the rest.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_runtime_cleanup_with_nothing_revoked_does_no_work(monkeypatch):
    monkeypatch.setattr(
        "app.services.workspace_mcp_oauth.revoke_workspace_grant_runtime",
        lambda *_: pytest.fail("should not be called"),
    )
    await workspace_mcp_revocation.cleanup_revoked_grant_runtime(object(), [])


def test_prompt_window_is_bounded():
    """A prompt nobody acted on must eventually stop following them around."""
    assert 1 <= workspace_mcp_revocation.RECONNECT_PROMPT_DAYS <= 90

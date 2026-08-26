import pytest

from clarity_agent import __main__ as cli


@pytest.mark.asyncio
async def test_registration_closes_client_on_the_same_async_lifecycle(monkeypatch):
    events = []

    class _Client:
        def __init__(self, config):
            events.append(("open", config))

        async def register(self, pairing_code, agent_info):
            events.append(("register", pairing_code, agent_info))
            return {"agent_id": "agent-1", "api_key": "secret"}

        async def close(self):
            events.append(("close",))

    config = object()
    monkeypatch.setattr(cli, "SaaSClient", _Client)

    result = await cli._register_with_saas(config, "PAIR-CODE", {"hostname": "fs01"})

    assert result == {"agent_id": "agent-1", "api_key": "secret"}
    assert events == [
        ("open", config),
        ("register", "PAIR-CODE", {"hostname": "fs01"}),
        ("close",),
    ]


@pytest.mark.asyncio
async def test_registration_closes_client_after_failure(monkeypatch):
    events = []

    class _Client:
        def __init__(self, config):
            pass

        async def register(self, pairing_code, agent_info):
            raise RuntimeError("network failed")

        async def close(self):
            events.append("close")

    monkeypatch.setattr(cli, "SaaSClient", _Client)

    with pytest.raises(RuntimeError, match="network failed"):
        await cli._register_with_saas(object(), "PAIR-CODE", {})

    assert events == ["close"]

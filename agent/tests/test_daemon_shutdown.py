import asyncio
from types import SimpleNamespace

import pytest

from clarity_agent import __main__ as agent_main


class _Ledger:
    instances = []

    def __init__(self, _path):
        self.closed = False
        self.__class__.instances.append(self)

    async def init(self):
        pass

    async def close(self):
        self.closed = True


class _Client:
    instances = []

    def __init__(self, _config):
        self.closed = False
        self.__class__.instances.append(self)

    async def close(self):
        self.closed = True

    async def get_shares(self):
        return []


class _Shares:
    async def refresh(self):
        return []

    async def get(self):
        await asyncio.Event().wait()


class _Scanner:
    def __init__(self, *_args):
        pass


class _Worker:
    unexpected = False

    def __init__(self, *_args, **_kwargs):
        pass

    async def poll_and_execute(self):
        if self.unexpected:
            raise asyncio.CancelledError()
        await asyncio.Event().wait()


class _Heartbeat:
    def __init__(self, *_args):
        pass

    async def send(self):
        await asyncio.Event().wait()


def _patch_daemon(monkeypatch):
    _Ledger.instances.clear()
    _Client.instances.clear()
    monkeypatch.setattr(agent_main, "FileLedger", _Ledger)
    monkeypatch.setattr(agent_main, "SaaSClient", _Client)
    monkeypatch.setattr(agent_main, "SmbScanner", _Scanner)
    monkeypatch.setattr(agent_main, "SmbReader", object)
    monkeypatch.setattr(agent_main, "ShareCache", lambda *_args: _Shares())
    monkeypatch.setattr(agent_main, "TaskWorker", _Worker)
    monkeypatch.setattr(agent_main, "HeartbeatService", _Heartbeat)
    monkeypatch.setattr(agent_main, "setup_logging", lambda *_args: None)


def _config():
    return SimpleNamespace(
        log_level="INFO",
        ledger_path="unused",
        saas_url="https://example.test",
        api_key="unused",
        agent_id="unused",
        task_poll_interval_seconds=60,
        heartbeat_interval_seconds=60,
        scan_interval_minutes=60,
    )


@pytest.mark.asyncio
async def test_run_daemon_stop_cancels_workers_and_closes_resources(monkeypatch):
    _patch_daemon(monkeypatch)
    stop = asyncio.Event()
    stop.set()

    await agent_main.run_daemon(_config(), stop_event=stop)

    assert _Ledger.instances[0].closed
    assert _Client.instances[0].closed
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("lawhand-")
    ]


@pytest.mark.asyncio
async def test_run_daemon_unexpected_worker_completion_cleans_resources(monkeypatch):
    _patch_daemon(monkeypatch)
    _Worker.unexpected = True
    try:
        await agent_main.run_daemon(_config())
    finally:
        _Worker.unexpected = False

    assert _Ledger.instances[0].closed
    assert _Client.instances[0].closed
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("lawhand-")
    ]


@pytest.mark.asyncio
async def test_run_daemon_external_cancellation_cleans_workers_and_resources(
    monkeypatch,
):
    _patch_daemon(monkeypatch)
    daemon = asyncio.create_task(agent_main.run_daemon(_config()))
    for _ in range(100):
        if _Ledger.instances and any(
            task.get_name().startswith("lawhand-") for task in asyncio.all_tasks()
        ):
            break
        await asyncio.sleep(0)

    daemon.cancel()
    with pytest.raises(asyncio.CancelledError):
        await daemon

    assert _Ledger.instances[0].closed
    assert _Client.instances[0].closed
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("lawhand-")
    ]


@pytest.mark.asyncio
async def test_search_node_startup_failure_closes_initialized_ledger(monkeypatch):
    _patch_daemon(monkeypatch)

    class _FailingSearchNode:
        @classmethod
        def from_config(cls, _config):
            raise ValueError("invalid search node")

    monkeypatch.setattr(agent_main, "SearchNode", _FailingSearchNode)
    config = _config()
    config.search_node_enabled = True
    config.local_index_enabled = False
    with pytest.raises(ValueError, match="invalid search node"):
        await agent_main.run_daemon(config)
    assert _Ledger.instances[0].closed


@pytest.mark.asyncio
async def test_saas_client_construction_failure_closes_started_resources(monkeypatch):
    _patch_daemon(monkeypatch)

    class _SearchNodeInstance:
        def __init__(self):
            self.closed = False
            self.gateway = SimpleNamespace(host="127.0.0.1", port=8765)

        async def start(self):
            pass

        async def close(self):
            self.closed = True

    search_node = _SearchNodeInstance()

    class _SearchNode:
        @classmethod
        def from_config(cls, _config):
            return search_node

    class _FailingClient:
        def __init__(self, _config):
            raise ValueError("invalid SaaS URL")

    monkeypatch.setattr(agent_main, "SearchNode", _SearchNode)
    monkeypatch.setattr(agent_main, "SaaSClient", _FailingClient)
    config = _config()
    config.search_node_enabled = True
    config.local_index_enabled = False
    with pytest.raises(ValueError, match="invalid SaaS URL"):
        await agent_main.run_daemon(config)
    assert _Ledger.instances[0].closed
    assert search_node.closed


@pytest.mark.asyncio
async def test_cleanup_attempts_every_resource_when_one_close_fails():
    calls = []

    class _Resource:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        async def close(self):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} close failed")

    await agent_main._close_daemon_resources(
        _Resource("ledger"),
        _Resource("local", fail=True),
        _Resource("search"),
        _Resource("client"),
    )
    assert set(calls) == {"ledger", "local", "search", "client"}


@pytest.mark.asyncio
async def test_startup_preserves_original_error_when_cleanup_fails(monkeypatch):
    _patch_daemon(monkeypatch)

    class _FailingCloseIndex:
        def __init__(self, *_args, **_kwargs):
            pass

        async def init(self):
            pass

        async def close(self):
            raise RuntimeError("cleanup failed")

    class _FailingSearchNode:
        @classmethod
        def from_config(cls, _config):
            raise ValueError("original startup failure")

    monkeypatch.setattr(agent_main, "LocalSearchIndex", _FailingCloseIndex)
    monkeypatch.setattr(agent_main, "SearchNode", _FailingSearchNode)
    config = _config()
    config.search_node_enabled = True
    config.local_index_enabled = True
    config.local_index_path = "unused"
    config.local_index_max_file_mb = 1
    config.local_index_workers = 1
    with pytest.raises(ValueError, match="original startup failure"):
        await agent_main.run_daemon(config)
    assert _Ledger.instances[0].closed

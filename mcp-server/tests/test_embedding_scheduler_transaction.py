import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import embedding_scheduler  # noqa: E402
from mcp_server.embedding_scheduler import SchedulerConfig  # noqa: E402


def test_scheduler_commits_count_snapshot_before_long_dispatch(monkeypatch):
    events: list[str] = []

    class Connection:
        def commit(self):
            events.append("commit")

    monkeypatch.setattr(embedding_scheduler, "acquire_lock", lambda *_args: True)
    monkeypatch.setattr(
        embedding_scheduler, "unembedded_chunk_count", lambda *_args: 42
    )
    monkeypatch.setattr(
        embedding_scheduler,
        "jetson_target_specs_from_env",
        lambda *_args: [object()],
    )
    monkeypatch.setattr(
        embedding_scheduler,
        "release_lock",
        lambda *_args: events.append("release"),
    )

    result = embedding_scheduler.run_scheduler_once(
        Connection(),
        SchedulerConfig(db_url="postgresql://example/db"),
        dispatch=lambda *_args, **_kwargs: events.append("dispatch"),
    )

    assert result.dispatched is True
    assert events == ["commit", "dispatch", "release"]

from types import SimpleNamespace

import pytest

from app.services.smb import _commit_audit_then_publish, _commit_result_then_publish


@pytest.mark.asyncio
async def test_content_task_is_published_only_after_audit_commit():
    events = []

    class _Db:
        async def commit(self):
            events.append("commit")

    class _Redis:
        async def set(self, key, value, ex=None):
            events.append("publish")
            self.call = SimpleNamespace(key=key, value=value, ex=ex)

    redis = _Redis()
    await _commit_audit_then_publish(_Db(), redis, "pending-key", {"task_id": "task-1"})

    assert events == ["commit", "publish"]
    assert redis.call.key == "pending-key"
    assert redis.call.ex == 300


@pytest.mark.asyncio
async def test_content_task_is_not_published_when_audit_commit_fails():
    class _Db:
        async def commit(self):
            raise RuntimeError("commit failed")

    class _Redis:
        async def set(self, *args, **kwargs):
            raise AssertionError("task must not be published")

    with pytest.raises(RuntimeError, match="commit failed"):
        await _commit_audit_then_publish(
            _Db(), _Redis(), "pending-key", {"task_id": "task-1"}
        )


@pytest.mark.asyncio
async def test_content_result_is_visible_only_after_audit_update_commit():
    events = []

    class _Db:
        async def commit(self):
            events.append("commit")

    class _Redis:
        async def set(self, key, value, ex=None):
            events.append("publish")

        async def delete(self, key):
            events.append("ack")

    await _commit_result_then_publish(
        _Db(), _Redis(), "result-key", "pending-key", "payload", 300
    )

    assert events == ["commit", "publish", "ack"]


@pytest.mark.asyncio
async def test_content_result_keeps_pending_task_when_publish_fails():
    events = []

    class _Db:
        async def commit(self):
            events.append("commit")

    class _Redis:
        async def set(self, key, value, ex=None):
            events.append("publish")
            raise RuntimeError("redis failed")

        async def delete(self, key):
            raise AssertionError("pending task must remain retryable")

    with pytest.raises(RuntimeError, match="redis failed"):
        await _commit_result_then_publish(
            _Db(), _Redis(), "result-key", "pending-key", "payload", 300
        )

    assert events == ["commit", "publish"]

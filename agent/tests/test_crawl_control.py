"""Fault, restart, identity, and reconciliation coverage for crawl control."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from clarity_agent.crawl_control import (
    ChangeHint,
    CrawlManifest,
    CrawlPipeline,
    ExtractedDocument,
    FileStat,
    JobKind,
    SourceRoot,
)


class MemorySource:
    def __init__(self, files: dict[str, tuple[FileStat, bytes]]) -> None:
        self.files = files
        self.fail_walk = False
        self.mutate_on_read = False

    async def discover(self, source: SourceRoot) -> AsyncIterator[FileStat]:
        for stat, _ in list(self.files.values()):
            yield stat
        if self.fail_walk:
            raise OSError("injected disconnect")

    async def stat(self, source: SourceRoot, path: str) -> FileStat | None:
        item = self.files.get(path)
        return item[0] if item else None

    async def read(self, source: SourceRoot, path: str) -> bytes:
        stat, content = self.files[path]
        if self.mutate_on_read:
            self.files[path] = (
                FileStat(
                    path, stat.size + 1, stat.modified_ns + 1, stable_id=stat.stable_id
                ),
                content + b"!",
            )
        return content


class Extractor:
    def __init__(self) -> None:
        self.calls = []

    async def extract(self, request):
        self.calls.append(request)
        return ExtractedDocument(
            request.content_version, {"text": request.content.decode()}
        )


class Index:
    def __init__(self) -> None:
        self.upserts = []
        self.deletes = []

    async def upsert(self, document):
        self.upserts.append(document)

    async def delete(self, source_id, file_id, content_version):
        self.deletes.append((source_id, file_id, content_version))


async def setup(tmp_path, files, **source_kwargs):
    manifest = CrawlManifest(str(tmp_path / "private" / "crawl.db"))
    await manifest.initialize()
    adapter = MemorySource(files)
    extractor = Extractor()
    index = Index()
    pipeline = CrawlPipeline(manifest, adapter, extractor, index, enabled=True)
    source = SourceRoot("share-a", r"\\server\share", **source_kwargs)
    await pipeline.add_source(source)
    return pipeline, manifest, adapter, extractor, index, source


@pytest.mark.asyncio
async def test_source_root_crawl_without_matter_and_interface_handoffs(tmp_path):
    stat = FileStat(r"\\server\share\brief.txt", 5, 10, stable_id="file-1")
    pipeline, manifest, _, extractor, index, _ = await setup(
        tmp_path, {stat.path: (stat, b"hello")}
    )

    assert await pipeline.reconcile("share-a") == 1
    assert await pipeline.process_one(JobKind.EXTRACT)
    assert await pipeline.process_one(JobKind.INDEX)

    assert extractor.calls[0].matter_ids == ()
    assert index.upserts[0].extracted == {"text": "hello"}
    assert (await manifest.status())["jobs"]["extract.done"] == 1


@pytest.mark.asyncio
async def test_restart_recovers_expired_lease_and_completion_is_token_fenced(tmp_path):
    stat = FileStat("a.txt", 1, 1, stable_id="one")
    _, manifest, _, _, _, source = await setup(tmp_path, {})
    await manifest.observe(source, stat, None)
    first = await manifest.claim(JobKind.EXTRACT, lease_seconds=-1)
    assert first is not None

    restarted = CrawlManifest(str(tmp_path / "private" / "crawl.db"))
    await restarted.initialize()
    second = await restarted.claim(JobKind.EXTRACT)
    assert second is not None and second.job_id == first.job_id
    assert second.lease_token != first.lease_token
    assert not await restarted.complete(first)
    assert await restarted.complete(second)


@pytest.mark.asyncio
async def test_partial_reconciliation_never_tombstones(tmp_path):
    first = FileStat("a.txt", 1, 1, stable_id="one")
    second = FileStat("b.txt", 1, 1, stable_id="two")
    pipeline, manifest, adapter, _, _, _ = await setup(
        tmp_path, {first.path: (first, b"a"), second.path: (second, b"b")}
    )
    await pipeline.reconcile("share-a")
    adapter.files.pop(second.path)
    adapter.fail_walk = True

    with pytest.raises(OSError, match="disconnect"):
        await pipeline.reconcile("share-a")

    assert not (await manifest.file("share-a", second.identity))["tombstoned"]
    assert (await manifest.source_state("share-a"))["reconciliation_required"]


@pytest.mark.asyncio
async def test_successful_reconciliation_tombstones_and_deletes(tmp_path):
    stat = FileStat("gone.txt", 1, 1, stable_id="gone")
    pipeline, manifest, adapter, _, index, _ = await setup(
        tmp_path, {stat.path: (stat, b"x")}
    )
    await pipeline.reconcile("share-a")
    adapter.files.clear()
    await pipeline.reconcile("share-a")

    assert (await manifest.file("share-a", stat.identity))["tombstoned"]
    assert await pipeline.process_one(JobKind.DELETE)
    assert index.deletes[0][1] == stat.identity


@pytest.mark.asyncio
async def test_stable_identity_tracks_rename_without_new_file(tmp_path):
    old = FileStat("old.txt", 3, 5, stable_id="same")
    pipeline, manifest, adapter, _, _, _ = await setup(
        tmp_path, {old.path: (old, b"old")}
    )
    await pipeline.reconcile("share-a")
    renamed = FileStat("new.txt", 3, 5, stable_id="same")
    adapter.files = {renamed.path: (renamed, b"old")}
    await pipeline.reconcile("share-a")

    row = await manifest.file("share-a", old.identity)
    assert row["path"] == "new.txt"
    assert not row["tombstoned"]
    assert (await manifest.status())["jobs"]["extract.ready"] == 2


@pytest.mark.asyncio
async def test_pre_post_read_change_retries_without_extractor_handoff(tmp_path):
    stat = FileStat("moving.txt", 4, 1, stable_id="moving")
    pipeline, manifest, adapter, extractor, _, _ = await setup(
        tmp_path, {stat.path: (stat, b"data")}
    )
    await pipeline.reconcile("share-a")
    adapter.mutate_on_read = True

    assert await pipeline.process_one(JobKind.EXTRACT)

    assert extractor.calls == []
    jobs = (await manifest.status())["jobs"]
    assert jobs["extract.done"] == 1
    assert jobs["extract.ready"] == 1


@pytest.mark.asyncio
async def test_hint_overflow_persists_cursor_and_forces_reconciliation(tmp_path):
    pipeline, manifest, _, _, _, _ = await setup(tmp_path, {})
    await pipeline.reconcile("share-a")
    await pipeline.apply_hint(ChangeHint("share-a", cursor="usn-42", overflow=True))

    state = await manifest.source_state("share-a")
    assert state["cursor"] == "usn-42"
    assert state["reconciliation_required"]
    assert "share-a" in await pipeline.due_sources(datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_path_reuse_preserves_old_identity_for_safe_delete(tmp_path):
    old = FileStat("same.txt", 1, 1, stable_id="old")
    pipeline, manifest, adapter, _, _, _ = await setup(
        tmp_path, {old.path: (old, b"a")}
    )
    await pipeline.reconcile("share-a")
    new = FileStat("same.txt", 1, 2, stable_id="new")
    adapter.files = {new.path: (new, b"b")}
    await pipeline.reconcile("share-a")

    old_row = await manifest.file("share-a", old.identity)
    new_row = await manifest.file("share-a", new.identity)
    assert old_row["tombstoned"]
    assert "#reused-" in old_row["path"]
    assert new_row["path"] == "same.txt"


@pytest.mark.asyncio
async def test_backpressure_fails_reconcile_without_tombstones(tmp_path):
    one = FileStat("one.txt", 1, 1, stable_id="one")
    two = FileStat("two.txt", 1, 1, stable_id="two")
    pipeline, manifest, _, _, _, _ = await setup(
        tmp_path,
        {one.path: (one, b"1"), two.path: (two, b"2")},
        max_pending_jobs=1,
    )
    with pytest.raises(RuntimeError, match="backpressure"):
        await pipeline.reconcile("share-a")
    assert (await manifest.source_state("share-a"))["reconciliation_required"]


@pytest.mark.asyncio
async def test_default_off_and_dead_letter(tmp_path):
    manifest = CrawlManifest(str(tmp_path / "private" / "crawl.db"))
    await manifest.initialize()
    pipeline = CrawlPipeline(manifest, MemorySource({}), Extractor(), Index())
    source = SourceRoot("s", "root")
    await pipeline.add_source(source)
    with pytest.raises(RuntimeError, match="disabled"):
        await pipeline.reconcile("s")

    await manifest.enqueue(JobKind.STAT, "s", path="missing")
    job = await manifest.claim(JobKind.STAT)
    await manifest.retry(job, OSError("injected"), max_attempts=1)
    assert (await manifest.status())["jobs"]["stat.dead"] == 1


@pytest.mark.asyncio
async def test_per_source_schedule_persists_discovery_job(tmp_path):
    pipeline, manifest, _, _, _, _ = await setup(tmp_path, {})
    now = datetime.now(timezone.utc)
    assert await pipeline.enqueue_due_reconciliations(now) == ["share-a"]
    assert (await manifest.status())["jobs"]["discover.ready"] == 1
    assert await pipeline.process_one(JobKind.DISCOVER)


@pytest.mark.asyncio
async def test_pause_is_durable_and_defers_work_without_burning_attempt(tmp_path):
    pipeline, manifest, _, _, _, _ = await setup(tmp_path, {})
    await manifest.enqueue(JobKind.STAT, "share-a", path="later.txt")
    await pipeline.pause("share-a")

    assert await pipeline.process_one(JobKind.STAT)
    status = await manifest.status()
    assert status["jobs"]["stat.retry"] == 1
    assert (await manifest.source_state("share-a"))["paused"]
    assert await pipeline.due_sources(datetime.now(timezone.utc)) == []

    await pipeline.resume("share-a")
    assert not (await manifest.source_state("share-a"))["paused"]

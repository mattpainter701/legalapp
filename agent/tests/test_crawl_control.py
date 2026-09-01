"""Fault, restart, identity, and reconciliation coverage for crawl control."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

import clarity_agent.search_control as search_control
from clarity_agent.crawl_control import (
    ChangeHint,
    CrawlManifest,
    CrawlPipeline,
    ExtractedDocument,
    FileStat,
    JobKind,
    LeaseLost,
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

    async def read_chunks(
        self, source: SourceRoot, path: str, chunk_size: int
    ) -> AsyncIterator[bytes]:
        stat, content = self.files[path]
        if self.mutate_on_read:
            self.files[path] = (
                FileStat(
                    path, stat.size + 1, stat.modified_ns + 1, stable_id=stat.stable_id
                ),
                content + b"!",
            )
        for offset in range(0, len(content), chunk_size):
            yield content[offset : offset + chunk_size]


class Extractor:
    def __init__(self) -> None:
        self.calls = []

    async def extract(self, request):
        self.calls.append(request)
        return ExtractedDocument(
            request.content_version,
            request.mutation_generation,
            f"artifact://{request.source_id}/{request.file_id}/{request.content_version}",
            request.fingerprint,
        )


class Index:
    def __init__(self) -> None:
        self.upserts = []
        self.deletes = []

    async def upsert(self, document):
        self.upserts.append(document)

    async def delete(self, source_id, file_id, mutation_generation):
        self.deletes.append((source_id, file_id, mutation_generation))


class Acl:
    def __init__(self) -> None:
        self.refreshes = []

    async def refresh(self, source_id, file_id, path, acl_version, mutation_generation):
        self.refreshes.append(
            (source_id, file_id, path, acl_version, mutation_generation)
        )


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
    stat = FileStat("brief.txt", 5, 10, stable_id="file-1")
    pipeline, manifest, _, extractor, index, _ = await setup(
        tmp_path, {stat.path: (stat, b"hello")}
    )

    assert await pipeline.reconcile("share-a") == 1
    assert await pipeline.process_one(JobKind.EXTRACT)
    assert await pipeline.process_one(JobKind.INDEX)

    assert extractor.calls[0].matter_ids == ()
    assert index.upserts[0].artifact_ref.startswith("artifact://share-a/")
    assert index.upserts[0].mutation_generation == 1
    assert (await manifest.status())["jobs"]["extract.done"] == 1

    with sqlite3.connect(manifest.path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    assert not columns.intersection({"payload_json", "content", "body", "snippet"})
    assert b"hello" not in manifest.path.read_bytes()


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
async def test_lease_renewal_is_generation_fenced(tmp_path):
    stat = FileStat("a.txt", 1, 1, stable_id="one")
    _, manifest, _, _, _, source = await setup(tmp_path, {})
    await manifest.observe(source, stat, None)
    expired = await manifest.claim(JobKind.EXTRACT, lease_seconds=-1)
    current = await manifest.claim(JobKind.EXTRACT)

    assert not await manifest.renew(expired)
    assert await manifest.renew(current, lease_seconds=120)


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


@pytest.mark.asyncio
async def test_reconciliation_lease_fences_overlapping_instances(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("clarity_agent.crawl_control.time.time", lambda: clock[0])
    path = str(tmp_path / "private" / "crawl.db")
    first = CrawlManifest(path)
    await first.initialize()
    source = SourceRoot("share-a", r"\\server\share")
    await first.register_source(source)
    second = CrawlManifest(path)
    await second.initialize()

    old_run = await first.begin_reconciliation("share-a", lease_seconds=1)
    with pytest.raises(RuntimeError, match="already leased"):
        await second.begin_reconciliation("share-a", lease_seconds=1)

    clock[0] += 2
    current_run = await second.begin_reconciliation("share-a", lease_seconds=10)
    stat = FileStat("current.txt", 1, 1, stable_id="current")
    await second.observe(source, stat, current_run)
    await second.finish_reconciliation("share-a", current_run)

    with pytest.raises(LeaseLost):
        await first.finish_reconciliation("share-a", old_run)
    assert not (await first.file("share-a", stat.identity))["tombstoned"]


@pytest.mark.asyncio
async def test_targeted_stat_during_reconciliation_is_seen_by_active_run(tmp_path):
    _, manifest, _, _, _, source = await setup(tmp_path, {})
    run_id = await manifest.begin_reconciliation(source.source_id)
    stat = FileStat("hinted-during-walk.txt", 1, 1, stable_id="hinted")
    await manifest.observe(source, stat, None)
    assert await manifest.finish_reconciliation(source.source_id, run_id) == 0
    assert not (await manifest.file(source.source_id, stat.identity))["tombstoned"]


@pytest.mark.asyncio
async def test_duplicate_enqueue_preserves_leased_and_dead_state(tmp_path):
    _, manifest, _, _, _, source = await setup(tmp_path, {})
    stat = FileStat("a.txt", 1, 1, stable_id="one")
    file_id, version, _ = await manifest.observe(source, stat, None)
    row = await manifest.file(source.source_id, file_id)
    job = await manifest.claim(JobKind.EXTRACT)

    await manifest.enqueue(
        JobKind.EXTRACT,
        source.source_id,
        file_id=file_id,
        path="a.txt",
        content_version=version,
        mutation_generation=row["mutation_generation"],
    )
    assert await manifest.claim(JobKind.EXTRACT) is None
    assert await manifest.renew(job)

    await manifest.retry(job, OSError("permanent"), max_attempts=1)
    await manifest.enqueue(
        JobKind.EXTRACT,
        source.source_id,
        file_id=file_id,
        path="a.txt",
        content_version=version,
        mutation_generation=row["mutation_generation"],
    )
    assert await manifest.claim(JobKind.EXTRACT) is None
    assert (await manifest.status())["jobs"]["extract.dead"] == 1


@pytest.mark.asyncio
async def test_pipeline_renews_lease_while_extractor_is_slow(tmp_path):
    class SlowExtractor(Extractor):
        async def extract(self, request):
            await asyncio.sleep(0.35)
            return await super().extract(request)

    path = str(tmp_path / "private" / "crawl.db")
    manifest = CrawlManifest(path)
    await manifest.initialize()
    stat = FileStat("slow.txt", 1, 1, stable_id="slow")
    adapter = MemorySource({stat.path: (stat, b"x")})
    pipeline = CrawlPipeline(
        manifest,
        adapter,
        SlowExtractor(),
        Index(),
        enabled=True,
        lease_seconds=0.15,
    )
    source = SourceRoot("share-a", r"\\server\share")
    await pipeline.add_source(source)
    await pipeline.reconcile(source.source_id)

    running = asyncio.create_task(pipeline.process_one(JobKind.EXTRACT))
    await asyncio.sleep(0.25)
    competing = CrawlManifest(path)
    await competing.initialize()
    assert await competing.claim(JobKind.EXTRACT) is None
    assert await running


@pytest.mark.asyncio
async def test_delayed_delete_cannot_remove_reappeared_generation(tmp_path):
    stat = FileStat("returning.txt", 1, 1, stable_id="same")
    pipeline, _, adapter, _, index, _ = await setup(tmp_path, {stat.path: (stat, b"a")})
    await pipeline.reconcile("share-a")
    adapter.files.clear()
    await pipeline.reconcile("share-a")
    adapter.files[stat.path] = (
        FileStat(stat.path, 1, 2, stable_id="same"),
        b"b",
    )
    await pipeline.reconcile("share-a")

    assert await pipeline.process_one(JobKind.DELETE)
    assert index.deletes == []


@pytest.mark.asyncio
async def test_change_before_read_is_reobserved_under_new_generation(tmp_path):
    old = FileStat("changed.txt", 1, 1, stable_id="same")
    pipeline, manifest, adapter, extractor, _, _ = await setup(
        tmp_path, {old.path: (old, b"a")}
    )
    await pipeline.reconcile("share-a")
    adapter.files[old.path] = (
        FileStat(old.path, 1, 2, stable_id="same"),
        b"b",
    )

    assert await pipeline.process_one(JobKind.EXTRACT)
    assert extractor.calls == []
    row = await manifest.file("share-a", old.identity)
    assert row["modified_ns"] == 2
    assert row["mutation_generation"] == 2
    assert (await manifest.status())["jobs"]["extract.ready"] == 1


@pytest.mark.asyncio
async def test_cursorless_hints_rearm_same_path_atomically(tmp_path):
    stat = FileStat("hinted.txt", 1, 1, stable_id="hinted")
    pipeline, manifest, _, _, _, _ = await setup(tmp_path, {stat.path: (stat, b"x")})
    await pipeline.apply_hint(ChangeHint("share-a", path=stat.path))
    assert await pipeline.process_one(JobKind.STAT)
    await pipeline.apply_hint(ChangeHint("share-a", path=stat.path))
    assert await pipeline.process_one(JobKind.STAT)
    assert (await manifest.status())["jobs"]["stat.done"] == 2
    assert (await manifest.source_state("share-a"))["hint_generation"] == 2


@pytest.mark.asyncio
async def test_source_paths_are_relative_descendants_and_disabled_work_defers(tmp_path):
    pipeline, manifest, _, _, index, _ = await setup(tmp_path, {})
    with pytest.raises(ValueError, match="descendant|escapes"):
        await pipeline.apply_hint(ChangeHint("share-a", path="../outside.txt"))
    with pytest.raises(ValueError, match="escapes"):
        await pipeline.apply_hint(
            ChangeHint("share-a", path=r"\\server\sibling\outside.txt")
        )

    disabled = SourceRoot("disabled", r"\\server\disabled", enabled=False)
    await pipeline.add_source(disabled)
    await manifest.enqueue(JobKind.DELETE, "disabled", file_id="x", path="x")
    assert await pipeline.process_one(JobKind.DELETE)
    assert index.deletes == []
    assert (await manifest.status())["jobs"]["delete.retry"] == 1


def test_manifest_reuses_fail_closed_local_path_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(search_control, "_is_network_filesystem", lambda path: True)
    with pytest.raises(ValueError, match="local storage"):
        CrawlManifest(str(tmp_path / "private" / "crawl.db"))


@pytest.mark.asyncio
async def test_manifest_permission_failure_is_fatal(tmp_path, monkeypatch):
    manifest = CrawlManifest(str(tmp_path / "private" / "crawl.db"))

    def fail_restrict(path, *, required=False):
        assert required
        raise PermissionError("injected")

    monkeypatch.setattr("clarity_agent.crawl_control._restrict", fail_restrict)
    with pytest.raises(PermissionError, match="injected"):
        await manifest.initialize()


@pytest.mark.asyncio
async def test_file_size_limit_blocks_extraction_before_read(tmp_path):
    stat = FileStat("large.bin", 2, 1, stable_id="large")
    pipeline, _, _, extractor, _, _ = await setup(
        tmp_path,
        {stat.path: (stat, b"xx")},
        max_file_size=1,
    )
    await pipeline.reconcile("share-a")
    with pytest.raises(ValueError, match="extraction limit"):
        await pipeline.process_one(JobKind.EXTRACT)
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_stream_limit_rejects_provider_underreported_size(tmp_path):
    stat = FileStat("lying.bin", 1, 1, stable_id="lying")
    pipeline, _, _, extractor, _, _ = await setup(
        tmp_path,
        {stat.path: (stat, b"xx")},
        max_file_size=1,
    )
    await pipeline.reconcile("share-a")
    with pytest.raises(ValueError, match="extraction limit"):
        await pipeline.process_one(JobKind.EXTRACT)
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_initial_acl_refresh_is_generation_ordered(tmp_path):
    path = str(tmp_path / "private" / "crawl.db")
    manifest = CrawlManifest(path)
    await manifest.initialize()
    stat = FileStat("acl.txt", 1, 1, stable_id="acl", acl_version="v1")
    adapter = MemorySource({stat.path: (stat, b"x")})
    acl = Acl()
    pipeline = CrawlPipeline(
        manifest, adapter, Extractor(), Index(), acl_sink=acl, enabled=True
    )
    source = SourceRoot("share-a", r"\\server\share")
    await pipeline.add_source(source)
    await pipeline.reconcile(source.source_id)
    assert await pipeline.process_one(JobKind.ACL_REFRESH)
    assert acl.refreshes == [("share-a", "stable:acl", "acl.txt", "v1", 1)]


@pytest.mark.asyncio
async def test_scheduler_keeps_one_outstanding_reconciliation(tmp_path):
    pipeline, manifest, _, _, _, _ = await setup(tmp_path, {})
    now = datetime.now(timezone.utc)
    await pipeline.enqueue_due_reconciliations(now)
    await pipeline.enqueue_due_reconciliations(now)
    assert (await manifest.status())["jobs"]["discover.ready"] == 1

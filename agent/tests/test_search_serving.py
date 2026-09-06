"""Scanner manifest -> extraction -> OpenSearch -> portal permission contract."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
import pytest_asyncio

from clarity_agent.native_acl import normalize_sddl
from clarity_agent.search_engine import BulkResult, SearchHit
from clarity_agent.search_serving import OpenSearchServingIndex

ROOT = r"\\server\firm"
PATH = ROOT + r"\Matter\memo.txt"
SID = "S-1-5-21-1"
AUTH = SimpleNamespace(principal_sids=frozenset({SID}), source_ids=frozenset({"share"}))
SCOPES = [{"share_id": "share", "folder_path": "Matter"}]
SHARES = [{"share_id": "share", "share_path": ROOT}]


class Engine:
    def __init__(self):
        self.chunks = ()
        self.fail = False
        self.request = None

    async def bulk_index(self, chunks):
        self.chunks = chunks
        return BulkResult(0, ("failed",)) if self.fail else BulkResult(len(chunks))

    async def delete_documents(self, mutations):
        return len(mutations)

    async def search(self, request):
        self.request = request
        hits = tuple(
            SearchHit(
                c.document_id,
                c.chunk_id,
                c.share_id,
                c.relative_path,
                c.filename,
                c.extension,
                1.0,
                c.content,
                c.page_number,
                (),
                c.ordinal,
                c.document_version,
            )
            for c in self.chunks
        )
        return SimpleNamespace(hits=hits)


@pytest_asyncio.fixture
async def serving(tmp_path):
    engine = Engine()
    from search_node.config import Settings, Limits

    settings = Settings(
        True,
        True,
        tmp_path / "temp",
        tmp_path / "staging",
        Limits(),
        ("eng",),
        20,
        6,
        0,
    )
    index = OpenSearchServingIndex(
        str(tmp_path / "manifest.db"), engine, extractor_settings=settings
    )
    try:
        await index.init()
        index.acl = normalize_sddl(f"D:(A;;FA;;;{SID})")

        async def fetch(job):
            return b"confidential pleading text"

        async def valid(job):
            return True

        index.start(fetch, path_validator=valid, acl_loader=lambda job: index.acl)
        await index.enqueue(
            dict(
                path=PATH,
                share_id="share",
                ext=".txt",
                content_hash="v1",
                size_bytes=26,
                modified_time="2026-09-06T00:00:00Z",
            )
        )
        async with asyncio.timeout(15):
            await index.wait_until_idle()
        yield index, engine
    finally:
        async with asyncio.timeout(15):
            await index.close()


async def search(index, **kwargs):
    return await index.search(
        "pleading", SCOPES, SHARES, ["txt"], 10, authorization=AUTH, **kwargs
    )


@pytest.mark.asyncio
async def test_serves_engine_text_with_empty_sqlite_fts(serving):
    index, engine = serving
    result = await search(index)
    assert result["index_state"] == "ready"
    assert result["hits"][0]["relative_path"] == r"Matter\memo.txt"
    assert result["hits"][0]["snippet"] == "confidential pleading text"
    assert (await index.stats())["fts_rows"] == 0
    assert engine.request.acl_tokens == (SID,)
    assert engine.request.filters.extensions == (".txt",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "denied",
        "unavailable",
        "stale",
        "future",
        "no_allow",
        "deleted",
        "superseded",
        "out_of_scope",
        "wrong_share",
    ],
)
async def test_no_metadata_or_text_escapes_when_access_or_version_changes(
    serving, failure
):
    index, engine = serving
    if failure == "denied":
        index.acl = normalize_sddl(f"D:(D;;FA;;;{SID})(A;;FA;;;{SID})")
    elif failure == "unavailable":
        index._acl_loader = lambda job: (_ for _ in ()).throw(OSError("offline"))
    elif failure in {"stale", "future"}:
        index.acl = {
            **index.acl,
            "captured_at": int(time.time()) + (100 if failure == "future" else -4000),
        }
    elif failure == "no_allow":
        index.acl = normalize_sddl("D:(A;;FA;;;S-1-5-21-2)")
    elif failure == "deleted":
        await index.delete([PATH])
    elif failure == "superseded":
        engine.chunks = tuple(
            replace(c, document_version="stale") for c in engine.chunks
        )
    elif failure == "out_of_scope":
        engine.chunks = tuple(
            replace(c, relative_path=ROOT + r"\Other\secret.txt") for c in engine.chunks
        )
    elif failure == "wrong_share":
        engine.chunks = tuple(replace(c, share_id="other") for c in engine.chunks)
    result = await search(index)
    assert result["hits"] == []
    assert result["index_state"] == "partial"


@pytest.mark.asyncio
async def test_requires_verified_identity_and_assigned_safe_scopes(serving):
    index, _ = serving
    with pytest.raises(ValueError, match="native authorization"):
        await index.search("x", SCOPES, SHARES, None, 10)
    for scopes in (
        [],
        [{"share_id": "other"}],
        [{"share_id": "share", "folder_path": "../Other"}],
    ):
        with pytest.raises(ValueError):
            await index.search("x", scopes, SHARES, None, 10, authorization=AUTH)
    index.available = False
    with pytest.raises(RuntimeError):
        await search(index)
    index.available = True


@pytest.mark.asyncio
async def test_unknown_acl_is_terminal_and_engine_failure_is_not_ready(serving):
    index, engine = serving
    job = dict(
        path=PATH,
        share_id="share",
        ext=".txt",
        content_hash="v2",
        size_bytes=26,
        modified_time="2026-09-06T00:00:00Z",
    )
    index.acl = {"state": "unavailable"}
    await index.enqueue(job)
    await index.wait_until_idle()
    assert (await search(index))["hits"] == []
    assert (await index.stats())["statuses"]["error"]["files"] == 1
    index.acl = normalize_sddl(f"D:(A;;FA;;;{SID})")
    engine.fail = True
    await index.enqueue(job)
    # Exercise a claimed publication directly rather than waiting retry backoff.
    for worker in index._workers:
        worker.cancel()
    import asyncio

    async with asyncio.timeout(15):
        await asyncio.gather(*index._workers, return_exceptions=True)
    index._workers.clear()
    job = await index._claim()
    with pytest.raises(RuntimeError, match="publish_incomplete"):
        await index._publish_text(job, [(None, 0, "text")], index.acl)
    assert (await search(index))["hits"] == []


@pytest.mark.asyncio
async def test_deletion_outbox_survives_engine_failure_and_retries_empty_scan(serving):
    index, engine = serving

    async def offline(mutations):
        raise RuntimeError("offline")

    engine.delete_documents = offline
    with pytest.raises(RuntimeError, match="offline"):
        await index.delete([PATH])
    assert (await search(index))["hits"] == []
    cursor = await index._db.execute("SELECT count(*) FROM pending_engine_deletions")
    assert (await cursor.fetchone())[0] == 1
    mutations = []

    async def recovered(batch):
        mutations.extend(batch)

    engine.delete_documents = recovered
    await index.delete([])
    assert len(mutations) == 1
    cursor = await index._db.execute("SELECT count(*) FROM pending_engine_deletions")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_newer_claim_publishes_before_old_extraction_without_stale_overwrite(
    serving,
):
    import asyncio

    index, engine = serving
    old_started, release_old = asyncio.Event(), asyncio.Event()
    old_extracted, old_discarded = asyncio.Event(), asyncio.Event()
    original_claim = index._claim

    async def claim():
        if old_extracted.is_set():
            old_discarded.set()
        return await original_claim()

    index._claim = claim
    original = index._extract_text

    async def extract(job, content):
        if job["content_hash"] == "old":
            old_started.set()
            await release_old.wait()
        result = await original(job, job["content_hash"].encode())
        if job["content_hash"] == "old":
            old_extracted.set()
        return result

    index._extract_text = extract
    index._workers.append(asyncio.create_task(index._run()))
    job = dict(
        path=PATH,
        share_id="share",
        ext=".txt",
        size_bytes=26,
        modified_time="2026-09-06T00:00:00Z",
        content_hash="old",
    )
    try:
        await index.enqueue(job)
        await asyncio.wait_for(old_started.wait(), timeout=5)
        await index.enqueue({**job, "content_hash": "new"})
        await index.wait_until_idle()
        current_version = engine.chunks[0].document_version
        assert engine.chunks[0].content == "new"
        release_old.set()
        await asyncio.wait_for(old_discarded.wait(), timeout=10)
        assert engine.chunks[0].document_version == current_version
        assert (await search(index))["hits"][0]["snippet"] == "new"
    finally:
        release_old.set()


@pytest.mark.asyncio
async def test_mixed_ocr_document_preserves_native_text_with_partial_coverage(
    serving, monkeypatch
):
    index, engine = serving
    record = SimpleNamespace(
        status="indexed-ready",
        ocr_pending_pages=(2,),
        sections=(SimpleNamespace(page_number=1, ordinal=0, text="native wording"),),
    )
    monkeypatch.setattr(index.extractor, "extract", lambda job: record)
    await index.enqueue(
        dict(
            path=PATH,
            share_id="share",
            ext=".txt",
            size_bytes=26,
            modified_time="2026-09-06T00:00:00Z",
            content_hash="mixed",
        )
    )
    await index.wait_until_idle()
    result = await search(index)
    assert result["hits"][0]["snippet"] == "native wording"
    assert result["index_state"] == "partial"
    assert (await index.stats())["statuses"]["ocr_pending"]["files"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_multi_delete_failure_rolls_back_outbox_and_keeps_scan_usable(
    serving, cancelled
):
    import asyncio
    import aiosqlite

    index, engine = serving
    second_path = ROOT + r"\Matter\second.txt"
    await index.enqueue(
        dict(
            path=second_path,
            share_id="share",
            ext=".txt",
            content_hash="second",
            size_bytes=26,
            modified_time="2026-09-06T00:00:00Z",
        )
    )
    await index.wait_until_idle()
    calls = []
    second_started = asyncio.Event()

    async def fail_second(batch):
        calls.extend(batch)
        if len(calls) == 2:
            second_started.set()
            if cancelled:
                await asyncio.Event().wait()
            raise RuntimeError("second delete unavailable")

    engine.delete_documents = fail_second
    task = asyncio.create_task(index.delete([PATH, second_path]))
    await asyncio.wait_for(second_started.wait(), timeout=5)
    if cancelled:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="second delete unavailable"):
            await task
    assert not index._db.in_transaction
    # Both source rows remain denied; both retry records are durably retained,
    # including the engine-acknowledged first deletion rolled back locally.
    assert (await search(index))["hits"] == []
    async with aiosqlite.connect(index.db_path) as observer:
        cursor = await observer.execute("SELECT count(*) FROM pending_engine_deletions")
        assert (await cursor.fetchone())[0] == 2
        cursor = await observer.execute("SELECT count(*) FROM index_files")
        assert (await cursor.fetchone())[0] == 0
    await index.enqueue(
        dict(
            path=ROOT + r"\Matter\new.bin",
            share_id="share",
            ext=".bin",
            content_hash="new",
            size_bytes=1,
            modified_time="2026-09-06T00:00:00Z",
        )
    )
    retried = []

    async def recovered(batch):
        retried.extend(batch)

    engine.delete_documents = recovered
    await index.delete([])
    assert {item.document_id for item in retried} == {
        item.document_id for item in calls
    }
    cursor = await index._db.execute("SELECT count(*) FROM pending_engine_deletions")
    assert (await cursor.fetchone())[0] == 0
    assert not index._db.in_transaction

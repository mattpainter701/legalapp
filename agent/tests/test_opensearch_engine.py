from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from clarity_agent.opensearch_engine import (
    OpenSearchEngine,
    OpenSearchLimits,
    RebuildReplayUncertain,
    SearchUnavailableError,
)
from clarity_agent.search_engine import (
    INDEX_SCHEMA_VERSION,
    BulkResult,
    DocumentChunk,
    DocumentMutation,
    SearchFilters,
    SearchRequest,
)

# Derived from the engine constant so a schema bump does not require a
# find-and-replace across this file again.
INDEX_PREFIX = "lawhand-firm-memory"
READ_ALIAS = f"{INDEX_PREFIX}-read-v{INDEX_SCHEMA_VERSION}"
WRITE_ALIAS = f"{INDEX_PREFIX}-write-v{INDEX_SCHEMA_VERSION}"
COORDINATION_INDEX = f"{INDEX_PREFIX}-coordination-v{INDEX_SCHEMA_VERSION}"
PHYSICAL_PREFIX = f"{INDEX_PREFIX}-v{INDEX_SCHEMA_VERSION}-"
ACTIVE_INDEX = f"{PHYSICAL_PREFIX}existing"


def _chunk(number: int = 1) -> DocumentChunk:
    return DocumentChunk(
        document_id="doc-1",
        chunk_id=f"doc-1:{number}",
        share_id="share-1",
        relative_path="Matters/Smith/brief.pdf",
        filename="brief.pdf",
        extension=".pdf",
        content="The motion for summary judgment was granted.",
        content_hash="abc",
        modified_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        mutation_generation=1,
        page_number=7,
        section_path=("Argument", "Standard of Review"),
        ordinal=number,
        matter_ids=("matter-1",),
        acl_tokens=("sid:S-1-5-21",),
    )


def _engine(**kwargs) -> OpenSearchEngine:
    return OpenSearchEngine(allow_insecure=True, **kwargs)


def _stub_rebuild_lease(engine: OpenSearchEngine) -> None:
    async def acquire(_source_index, _candidate_index, **_kwargs):
        return ("test-owner", 0, 1)

    async def update(_lease, **_updates):
        return None

    async def release(_lease):
        return None

    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release


def _stub_rebuild_source(engine: OpenSearchEngine, index: str = ACTIVE_INDEX) -> None:
    async def ensure_index():
        return index

    engine.ensure_index = ensure_index


def test_rejects_non_loopback_opensearch_endpoint():
    with pytest.raises(ValueError, match="loopback"):
        OpenSearchEngine("https://192.0.2.10:9200")


def test_requires_tls_complete_auth_and_no_url_credentials():
    with pytest.raises(ValueError, match="HTTPS"):
        OpenSearchEngine("http://127.0.0.1:9200", username="u", password="p")
    with pytest.raises(ValueError, match="configured together"):
        OpenSearchEngine("https://127.0.0.1:9200", username="u")
    with pytest.raises(ValueError, match="embedded"):
        OpenSearchEngine("https://u:p@127.0.0.1:9200")


def test_versioned_mapping_is_strict_bm25_and_page_aware():
    engine = _engine()
    mapping = engine._mapping()
    assert mapping["mappings"]["dynamic"] == "strict"
    assert (
        mapping["mappings"]["_meta"]["lawhand_schema_version"] == INDEX_SCHEMA_VERSION
    )
    assert mapping["settings"]["similarity"]["default"]["type"] == "BM25"
    properties = mapping["mappings"]["properties"]
    assert properties["chunks"]["type"] == "nested"
    assert properties["chunks"]["properties"]["content"]["type"] == "text"
    assert properties["document_version"]["type"] == "keyword"
    assert properties["chunks"]["properties"]["page_number"]["type"] == "integer"
    assert properties["chunks"]["properties"]["section_path"]["type"] == "keyword"
    assert properties["acl_tokens"]["type"] == "keyword"
    assert properties["deny_acl_tokens"]["type"] == "keyword"


def test_explicit_deny_beats_an_allow_from_another_group():
    """Windows resolves a DENY ACE ahead of any allow, including an inherited one."""
    engine = _engine()
    body = engine._search_body(
        SearchRequest(query="settlement", acl_tokens=("sid:S-1-5-21-1", "sid:S-1-5-32"))
    )
    clause = body["query"]["bool"]
    assert {"terms": {"acl_tokens": ["sid:S-1-5-21-1", "sid:S-1-5-32"]}} in clause[
        "filter"
    ]
    # The deny clause covers the caller's whole principal set, so being denied
    # through any one of their groups removes the document.
    assert clause["must_not"] == [
        {"terms": {"deny_acl_tokens": ["sid:S-1-5-21-1", "sid:S-1-5-32"]}}
    ]


def test_deny_tokens_travel_with_the_document_envelope():
    engine = _engine()
    source = engine._document_source(
        [replace(_chunk(1), acl_tokens=("allow",), deny_acl_tokens=("deny",))]
    )
    assert source["acl_tokens"] == ["allow"]
    assert source["deny_acl_tokens"] == ["deny"]
    # Deny is part of the atomic envelope, so a revocation cannot land as a
    # partial update that leaves the allow set behind.
    assert source["mutation_digest"]


def test_chunks_disagreeing_on_deny_tokens_are_rejected():
    engine = _engine()
    with pytest.raises(ValueError):
        engine._document_source(
            [
                replace(_chunk(1), deny_acl_tokens=("deny",)),
                replace(_chunk(2), deny_acl_tokens=()),
            ]
        )


def test_query_preserves_phrase_boolean_syntax_and_applies_acl_and_fields():
    engine = _engine()
    body = engine._search_body(
        SearchRequest(
            query='"summary judgment" AND granted',
            acl_tokens=("sid:S-1-5-21",),
            filters=SearchFilters(
                share_ids=("share-1",), matter_ids=("matter-1",), extensions=(".PDF",)
            ),
            limit=25,
        )
    )
    nested = body["query"]["bool"]["must"][0]["nested"]
    query = nested["query"]["query_string"]
    assert query["query"] == '"summary judgment" AND granted'
    assert query["default_operator"] == "AND"
    assert query["allow_leading_wildcard"] is False
    assert query["max_determinized_states"] == 1_000
    assert query["fuzzy_max_expansions"] == 20
    assert {"terms": {"acl_tokens": ["sid:S-1-5-21"]}} in body["query"]["bool"][
        "filter"
    ]
    assert {"terms": {"extension": [".pdf"]}} in body["query"]["bool"]["filter"]
    highlight = nested["inner_hits"]["highlight"]
    assert highlight["fields"]["chunks.content"]["fragment_size"] == 240
    assert highlight["encoder"] == "html"
    assert highlight["max_analyzer_offset"] == 1_000_000
    assert "max_analyzed_offset" not in highlight


@pytest.mark.asyncio
async def test_bulk_index_is_bounded_and_keeps_text_only_in_opensearch_request():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.startswith("/_alias/"):
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "aliases": {
                            READ_ALIAS: {},
                            WRITE_ALIAS: {"is_write_index": True},
                        }
                    }
                },
            )
        if request.url.path == f"/{ACTIVE_INDEX}/_mapping":
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "mappings": {
                            "_meta": {"lawhand_schema_version": INDEX_SCHEMA_VERSION}
                        }
                    }
                },
            )
        if request.url.path == "/_bulk":
            return httpx.Response(
                200,
                json={
                    "errors": False,
                    "items": [{"index": {"_id": "doc-1:1", "status": 201}}],
                },
            )
        if request.method == "PUT" and "/_doc/" in request.url.path:
            return httpx.Response(201, json={"result": "created"})
        if request.url.path.endswith("/_delete_by_query"):
            return httpx.Response(
                200,
                json={
                    "deleted": 1,
                    "total": 1,
                    "timed_out": False,
                    "version_conflicts": 0,
                    "failures": [],
                },
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    _stub_rebuild_lease(engine)
    result = await engine.bulk_index([_chunk()])
    assert result.accepted == 1
    bulk = next(request for request in requests if request.url.path == "/_bulk")
    lines = bulk.content.decode().splitlines()
    physical_id = json.loads(lines[0])["index"]["_id"]
    assert len(physical_id) == 64 and physical_id != "doc-1:1"
    source = json.loads(lines[1])
    assert source["record_type"] == "document"
    assert source["chunks"][0]["content"].startswith("The motion")
    assert source["document_version"] == "abc"
    assert source["chunks"][0]["page_number"] == 7
    assert source["chunks"][0]["section_path"] == ["Argument", "Standard of Review"]
    assert json.loads(lines[0])["index"]["version_type"] == "external"
    assert json.loads(lines[0])["index"]["version"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_swaps_aliases_only_after_refresh():
    events = []
    alias_actions = []
    active_index = ACTIVE_INDEX

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_index
        events.append((request.method, request.url.path))
        if request.url.path == f"/{ACTIVE_INDEX}/_block/write":
            return httpx.Response(
                200, json={"acknowledged": True, "shards_acknowledged": True}
            )
        if request.method == "PUT" and request.url.path.startswith(
            f"/{PHYSICAL_PREFIX}"
        ):
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path == "/_bulk":
            return httpx.Response(
                200, json={"items": [{"index": {"status": 201, "_id": "doc-1:1"}}]}
            )
        if request.url.path.endswith("/_refresh"):
            return httpx.Response(
                200,
                json={"_shards": {"total": 1, "successful": 1, "failed": 0}},
            )
        if request.url.path == "/_aliases":
            alias_actions.extend(json.loads(request.content)["actions"])
            active_index = alias_actions[-1]["add"]["index"]
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.startswith("/_alias/") and active_index:
            return httpx.Response(
                200,
                json={
                    active_index: {
                        "aliases": {
                            READ_ALIAS: {},
                            WRITE_ALIAS: {"is_write_index": True},
                        }
                    }
                },
            )
        if request.url.path.startswith("/_alias/") or request.method == "HEAD":
            return httpx.Response(404)
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    _stub_rebuild_lease(engine)
    _stub_rebuild_source(engine)

    async def write_blocked(_index):
        return False

    async def replay(_source, _destination, _lease):
        return {
            "total": 0,
            "created": 0,
            "updated": 0,
            "version_conflicts": 0,
            "noops": 0,
            "failures": [],
            "timed_out": False,
        }

    engine._index_write_blocked = write_blocked
    engine._replay_index = replay

    async def chunks():
        yield _chunk()

    index = await engine.rebuild(chunks())
    assert index.startswith(PHYSICAL_PREFIX)
    refresh_index = next(
        i for i, event in enumerate(events) if event[1].endswith("/_refresh")
    )
    alias_index = events.index(("POST", "/_aliases"))
    assert refresh_index < alias_index
    assert all(
        action.get("remove", {}).get("index", "").startswith(PHYSICAL_PREFIX)
        for action in alias_actions
        if "remove" in action
    )
    await client.aclose()


def test_bulk_and_result_limits_are_enforced():
    engine = _engine(limits=OpenSearchLimits(max_results=2, max_bulk_documents=1))
    body = engine._search_body(
        SearchRequest(query="contract", acl_tokens=("acl",), limit=99)
    )
    assert body["size"] == 2


def test_document_envelope_rejects_mixed_acl_or_metadata():
    engine = _engine()
    with pytest.raises(ValueError, match="share metadata and generation"):
        engine._document_source([_chunk(1), replace(_chunk(2), acl_tokens=("other",))])


@pytest.mark.asyncio
async def test_aliases_must_be_single_complete_and_write_enabled():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "old": {
                    "aliases": {
                        READ_ALIAS: {},
                        WRITE_ALIAS: {"is_write_index": True},
                    }
                },
                "current": {
                    "aliases": {READ_ALIAS: {}},
                },
            },
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    with pytest.raises(RuntimeError, match="one active index"):
        await engine.ensure_index()
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_or_split_aliases_fail_closed():
    def missing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"read-v{INDEX_SCHEMA_VERSION}"):
            return httpx.Response(
                200,
                json={ACTIVE_INDEX: {"aliases": {READ_ALIAS: {}}}},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200",
        transport=httpx.MockTransport(missing_handler),
    )
    engine = _engine(client=client)
    with pytest.raises(RuntimeError, match="incomplete"):
        await engine.ensure_index()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("index", "read_metadata", "write_metadata", "message"),
    [
        (
            "other-product-v1-index",
            {},
            {"is_write_index": True},
            "unexpected index",
        ),
        (
            ACTIVE_INDEX,
            {"filter": {"term": {"share_id": "share-1"}}},
            {"is_write_index": True},
            "unsafe routing or filters",
        ),
        (
            ACTIVE_INDEX,
            {},
            {"is_write_index": True, "routing": "tenant-1"},
            "unsafe routing or filters",
        ),
    ],
)
async def test_aliases_reject_foreign_indexes_filters_and_routing(
    index, read_metadata, write_metadata, message
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                index: {
                    "aliases": {
                        READ_ALIAS: read_metadata,
                        WRITE_ALIAS: write_metadata,
                    }
                }
            },
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    with pytest.raises(RuntimeError, match=message):
        await engine.ensure_index()
    await client.aclose()


@pytest.mark.asyncio
async def test_replacement_deletes_old_chunks_before_partial_bulk_failure():
    events = []
    bulk_calls = 0
    tombstone_requests: list[httpx.Request] = []
    document_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bulk_calls
        events.append(request.url.path)
        if request.method == "PUT" and "/_doc/" in request.url.path:
            return httpx.Response(201, json={"result": "created"})
        if request.url.path.startswith("/_alias/"):
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "aliases": {
                            READ_ALIAS: {},
                            WRITE_ALIAS: {"is_write_index": True},
                        }
                    }
                },
            )
        if request.url.path == f"/{ACTIVE_INDEX}/_mapping":
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "mappings": {
                            "_meta": {"lawhand_schema_version": INDEX_SCHEMA_VERSION}
                        }
                    }
                },
            )
        if request.url.path == "/_bulk":
            bulk_calls += 1
            lines = [json.loads(line) for line in request.content.splitlines()]
            sources = lines[1::2]
            if all(source.get("record_type") == "tombstone" for source in sources):
                tombstone_requests.append(request)
                return httpx.Response(
                    200,
                    json={"items": [{"index": {"status": 201}} for _ in sources]},
                )
            document_requests.append(request)
            # doc-1 loses its replacement; doc-2's must still land.
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "index": {
                                "status": 409
                                if source["document_id"] == "doc-1"
                                else 201
                            }
                        }
                        for source in sources
                    ]
                },
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    revoked_chunks = [
        replace(_chunk(1), acl_tokens=("sid:new",)),
        replace(_chunk(2), acl_tokens=("sid:new",)),
        replace(
            _chunk(1),
            document_id="doc-2",
            chunk_id="doc-2:1",
            acl_tokens=("sid:new",),
        ),
    ]
    result = await engine.bulk_index(revoked_chunks)
    assert result.accepted == 1 and result.failed_ids == ("doc-1:1", "doc-1:2")

    # Both documents were tombstoned before any replacement was attempted, so a
    # failed replacement cannot leave old authorized content searchable.
    assert len(tombstone_requests) == 1
    tombstoned = {
        json.loads(line)["document_id"]
        for line in tombstone_requests[0].content.splitlines()[1::2]
    }
    assert tombstoned == {"doc-1", "doc-2"}
    assert events.index("/_bulk") < len(events) - 1
    assert events.count("/_bulk") == 2

    # One refresh for the whole revocation, not one per document: the visibility
    # guarantee is unchanged but ingest no longer refreshes per document.
    assert tombstone_requests[0].url.params.get("refresh") == "true"
    assert document_requests[0].url.params.get("refresh") is None
    await client.aclose()


@pytest.mark.asyncio
async def test_health_reads_actual_watermarks_without_cluster_mutation():
    methods = []
    state = {
        "threshold": "true",
        "timed_out": False,
        "write_block": "false",
        "lease": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.url.path == "/_cluster/health":
            return httpx.Response(
                200, json={"status": "yellow", "timed_out": state["timed_out"]}
            )
        if request.url.path == "/_cluster/settings":
            return httpx.Response(
                200,
                json={
                    "defaults": {
                        "cluster.routing.allocation.disk.watermark.low": "80%",
                        "cluster.routing.allocation.disk.watermark.high": "90%",
                        "cluster.routing.allocation.disk.watermark.flood_stage": "95%",
                        "cluster.routing.allocation.disk.threshold_enabled": state[
                            "threshold"
                        ],
                    }
                },
            )
        if request.url.path == f"/{ACTIVE_INDEX}/_settings":
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "settings": {"index.blocks.write": state["write_block"]}
                    }
                },
            )
        if request.url.path.endswith("/_doc/rebuild-lock"):
            if state["lease"]:
                return httpx.Response(
                    200,
                    json={
                        "_source": {"owner": "owner", "phase": "quarantined"},
                        "_seq_no": 1,
                        "_primary_term": 1,
                    },
                )
            return httpx.Response(404)
        if request.url.path.startswith("/_alias/"):
            return httpx.Response(
                200,
                json={
                    ACTIVE_INDEX: {
                        "aliases": {
                            READ_ALIAS: {},
                            WRITE_ALIAS: {"is_write_index": True},
                        }
                    }
                },
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    health = await engine.health()
    assert health.status == "healthy"
    assert health.details["disk_watermarks"]["high"] == "90%"
    assert ("PUT", "/_cluster/settings") not in methods
    state["threshold"] = "false"
    assert (await engine.health()).status == "degraded"
    state["threshold"] = "true"
    state["timed_out"] = True
    assert (await engine.health()).status == "degraded"
    state["timed_out"] = False
    state["write_block"] = "true"
    blocked = await engine.health()
    assert blocked.status == "degraded"
    assert blocked.details["active_index_write_blocked"] is True
    state["write_block"] = "false"
    state["lease"] = True
    leased = await engine.health()
    assert leased.status == "degraded"
    assert leased.details["rebuild_lease_active"] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_delete_rejects_a_stale_generation():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"result": "conflict"},
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)

    async def ensure_index():
        return "idx"

    engine.ensure_index = ensure_index
    with pytest.raises(RuntimeError, match="stale"):
        await engine.delete_documents([DocumentMutation("doc-1", 1)])
    await client.aclose()


@pytest.mark.asyncio
async def test_search_rejects_partial_shard_results():
    engine = _engine()

    async def ensure_index():
        return "idx"

    engine.ensure_index = ensure_index

    async def request(*_args, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "http://127.0.0.1"),
            json={"_shards": {"failed": 1}, "hits": {"hits": [], "total": 0}},
        )

    engine._request = request

    async def lease_state():
        return None

    async def write_blocked(_index):
        return False

    engine._rebuild_lease_state = lease_state
    engine._index_write_blocked = write_blocked
    with pytest.raises(RuntimeError, match="incomplete"):
        await engine.search(SearchRequest(query="contract", acl_tokens=("acl",)))
    await engine.close()


@pytest.mark.asyncio
async def test_search_returns_provenance_from_best_nested_chunk():
    engine = _engine()

    async def ensure_index():
        return ACTIVE_INDEX

    async def request(*_args, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "http://127.0.0.1"),
            json={
                "_shards": {"failed": 0},
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_score": 4.5,
                            "_source": {
                                "document_id": "doc-1",
                                "share_id": "share-1",
                                "relative_path": "brief.pdf",
                                "filename": "brief.pdf",
                                "extension": ".pdf",
                            },
                            "inner_hits": {
                                "chunks": {
                                    "hits": {
                                        "hits": [
                                            {
                                                "_source": {
                                                    "chunk_id": "doc-1:7",
                                                    "page_number": 7,
                                                    "section_path": ["Argument"],
                                                    "ordinal": 3,
                                                },
                                                "highlight": {
                                                    "chunks.content": [
                                                        "<mark>summary judgment</mark>"
                                                    ]
                                                },
                                            }
                                        ]
                                    }
                                }
                            },
                        }
                    ],
                },
                "took": 4,
                "timed_out": False,
            },
        )

    engine.ensure_index = ensure_index
    engine._request = request

    async def lease_state():
        return None

    async def write_blocked(_index):
        return False

    engine._rebuild_lease_state = lease_state
    engine._index_write_blocked = write_blocked
    result = await engine.search(
        SearchRequest(query="summary judgment", acl_tokens=("allowed",))
    )
    assert result.total == 1
    assert result.hits[0].chunk_id == "doc-1:7"
    assert result.hits[0].page_number == 7
    assert result.hits[0].snippet == "<mark>summary judgment</mark>"
    await engine.close()


@pytest.mark.asyncio
async def test_alias_commit_recovers_after_committed_response_loss():
    active_index = None
    deletes = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_index
        if request.url.path == "/_aliases":
            active_index = json.loads(request.content)["actions"][-1]["add"]["index"]
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path.startswith("/_alias/"):
            return httpx.Response(
                200,
                json={
                    active_index: {
                        "aliases": {
                            READ_ALIAS: {},
                            WRITE_ALIAS: {"is_write_index": True},
                        }
                    }
                },
            )
        if request.method == "DELETE":
            deletes.append(request.url.path)
            return httpx.Response(200, json={"acknowledged": True})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    await engine._commit_aliases(f"{PHYSICAL_PREFIX}new")
    assert active_index == f"{PHYSICAL_PREFIX}new" and not deletes
    await client.aclose()


@pytest.mark.asyncio
async def test_alias_commit_lost_response_is_uncertain_even_if_old_alias_is_observed():
    async def swap_aliases(_index):
        raise httpx.ReadTimeout("response lost")

    async def active_index():
        return ACTIVE_INDEX

    engine = _engine()
    engine._swap_aliases = swap_aliases
    engine._active_index = active_index
    with pytest.raises(RebuildReplayUncertain, match="terminal outcome"):
        await engine._commit_aliases(f"{PHYSICAL_PREFIX}new")
    await engine.close()


@pytest.mark.asyncio
async def test_initialization_retains_cutover_lease_when_alias_outcome_is_unknown():
    engine = _engine()
    candidate = f"{PHYSICAL_PREFIX}initial"
    active_reads = 0
    phases = []
    released = False

    async def active_index():
        nonlocal active_reads
        active_reads += 1
        if active_reads <= 2:
            return None
        raise httpx.ReadTimeout("alias reconciliation unavailable")

    async def acquire(_source, _candidate, **_kwargs):
        return ("owner", 0, 1)

    async def update(_lease, **updates):
        if updates.get("phase"):
            phases.append(updates["phase"])

    async def release(_lease):
        nonlocal released
        released = True

    async def request(method, path, **_kwargs):
        return httpx.Response(200, request=httpx.Request(method, path), json={})

    async def swap_aliases(_index):
        raise httpx.ReadTimeout("alias response lost")

    engine._active_index = active_index
    engine._new_index_name = lambda: candidate
    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release
    engine._request = request
    engine._swap_aliases = swap_aliases

    with pytest.raises(RebuildReplayUncertain, match="outcome could not be verified"):
        await engine.ensure_index()
    assert phases == ["cutover"]
    assert released is False
    await engine.close()


@pytest.mark.asyncio
async def test_alias_lookup_uses_one_faithful_combined_response():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                ACTIVE_INDEX: {
                    "aliases": {
                        READ_ALIAS: {},
                        WRITE_ALIAS: {"is_write_index": True},
                    }
                }
            },
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    assert await engine._active_index() == ACTIVE_INDEX
    assert paths == [f"/_alias/{READ_ALIAS},{WRITE_ALIAS}"]
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_excludes_concurrent_delete_until_cutover():
    engine = _engine()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def rebuild_unlocked(_chunks):
        entered.set()
        await release.wait()
        return "new-index"

    async def ensure_index():
        return "new-index"

    async def write_tombstone(_alias, _mutation):
        return None

    engine._rebuild_unlocked = rebuild_unlocked
    engine.ensure_index = ensure_index
    engine._write_tombstone = write_tombstone

    async def no_chunks():
        if False:
            yield _chunk()

    rebuild_task = asyncio.create_task(engine.rebuild(no_chunks()))
    await entered.wait()
    delete_task = asyncio.create_task(
        engine.delete_documents([DocumentMutation("doc-1", 1)])
    )
    await asyncio.sleep(0)
    assert not delete_task.done()
    release.set()
    assert await rebuild_task == "new-index"
    assert await delete_task == 1
    await engine.close()


@pytest.mark.asyncio
async def test_rebuild_replays_mutation_from_second_engine_before_cutover():
    old_index = ACTIVE_INDEX
    new_index = f"{PHYSICAL_PREFIX}rebuild"
    stores = {old_index: {}, new_index: {}}
    block_entered = asyncio.Event()
    mutation_finished = asyncio.Event()
    engine_a = _engine()
    engine_b = _engine()

    async def active_index():
        return old_index

    async def request(method, path, **_kwargs):
        if method == "PUT" and path == f"/{old_index}/_block/write":
            block_entered.set()
            await mutation_finished.wait()
            return httpx.Response(
                200,
                request=httpx.Request(method, path),
                json={"acknowledged": True, "shards_acknowledged": True},
            )
        if method == "POST" and path.endswith("/_refresh"):
            return httpx.Response(
                200,
                request=httpx.Request(method, path),
                json={"_shards": {"total": 1, "successful": 1, "failed": 0}},
            )
        return httpx.Response(200, request=httpx.Request(method, path), json={})

    async def bulk_to(index, chunks):
        source = engine_a._document_source(chunks)
        stores[index] = {
            "version": source["mutation_generation"] * 2 + 1,
            "source": source,
        }
        return BulkResult(accepted=len(chunks))

    async def commit_aliases(index):
        assert index == new_index

    async def replay_index(source, destination, _lease):
        assert source == old_index and destination == new_index
        stores[new_index] = dict(stores[old_index])
        return {
            "total": 1,
            "created": 0,
            "updated": 1,
            "version_conflicts": 0,
            "noops": 0,
            "failures": [],
            "timed_out": False,
        }

    engine_a._active_index = active_index
    engine_a.ensure_index = active_index
    _stub_rebuild_lease(engine_a)
    engine_a._new_index_name = lambda: new_index
    engine_a._request = request
    engine_a._bulk_to = bulk_to
    engine_a._commit_aliases = commit_aliases
    engine_a._replay_index = replay_index

    async def ensure_old_index():
        return old_index

    async def write_tombstone(_alias, mutation):
        stores[old_index] = {
            "version": mutation.generation * 2,
            "source": {"record_type": "tombstone"},
        }

    async def write_newer(_alias, chunks):
        source = engine_b._document_source(chunks)
        stores[old_index] = {
            "version": source["mutation_generation"] * 2 + 1,
            "source": source,
        }
        return BulkResult(accepted=len(chunks))

    engine_b.ensure_index = ensure_old_index
    engine_b._write_tombstone = write_tombstone
    engine_b._bulk_to = write_newer

    async def chunks():
        yield replace(_chunk(), mutation_generation=1, acl_tokens=("sid:old",))

    rebuild = asyncio.create_task(engine_a.rebuild(chunks()))
    await block_entered.wait()
    await engine_b.bulk_index(
        [replace(_chunk(), mutation_generation=2, acl_tokens=("sid:new",))]
    )
    mutation_finished.set()
    assert await rebuild == new_index
    assert stores[new_index]["version"] == 5
    assert stores[new_index]["source"]["acl_tokens"] == ["sid:new"]
    await engine_a.close()
    await engine_b.close()


@pytest.mark.asyncio
async def test_first_rebuild_serializes_initialization_before_replaying_mutations():
    initial = f"{PHYSICAL_PREFIX}initial"
    candidate = f"{PHYSICAL_PREFIX}candidate"
    state = {"active": None, "lease_owner": None}
    stores = {initial: {}, candidate: {}}
    names = iter((initial, candidate))
    initialization_started = asyncio.Event()
    allow_initialization = asyncio.Event()
    block_entered = asyncio.Event()
    mutation_finished = asyncio.Event()
    engine_a = _engine()
    engine_b = _engine()

    async def active_index():
        return state["active"]

    async def validate_mapping(_index):
        return None

    async def acquire(_source, _candidate, **_kwargs):
        if state["lease_owner"] is not None:
            raise RuntimeError("another OpenSearch rebuild owns the lease")
        owner = f"owner-{_candidate}"
        state["lease_owner"] = owner
        return (owner, 0, 1)

    async def release(lease):
        assert state["lease_owner"] == lease[0]
        state["lease_owner"] = None

    async def update(_lease, **_updates):
        return None

    async def request(method, path, **_kwargs):
        if method == "PUT" and path == f"/{initial}":
            initialization_started.set()
            await allow_initialization.wait()
            return httpx.Response(200, request=httpx.Request(method, path), json={})
        if method == "PUT" and path == f"/{candidate}":
            return httpx.Response(200, request=httpx.Request(method, path), json={})
        if method == "PUT" and path == f"/{initial}/_block/write":
            block_entered.set()
            await mutation_finished.wait()
            return httpx.Response(
                200,
                request=httpx.Request(method, path),
                json={"acknowledged": True, "shards_acknowledged": True},
            )
        if method == "POST" and path.endswith("/_refresh"):
            return httpx.Response(
                200,
                request=httpx.Request(method, path),
                json={"_shards": {"total": 1, "successful": 1, "failed": 0}},
            )
        return httpx.Response(200, request=httpx.Request(method, path), json={})

    async def commit_aliases(index):
        state["active"] = index

    async def write_blocked(_index):
        return False

    async def bulk_to(index, chunks):
        source = engine_a._document_source(chunks)
        stores[index] = {
            "version": source["mutation_generation"] * 2 + 1,
            "source": source,
        }
        return BulkResult(accepted=len(chunks))

    async def replay(source, destination, _lease):
        stores[destination] = dict(stores[source])
        return {
            "total": 1,
            "created": 0,
            "updated": 1,
            "version_conflicts": 0,
            "noops": 0,
            "failures": [],
            "timed_out": False,
        }

    for engine in (engine_a, engine_b):
        engine._active_index = active_index
        engine._validate_active_mapping = validate_mapping
        engine._acquire_rebuild_lease = acquire
        engine._release_rebuild_lease = release
        engine._update_rebuild_lease = update
    engine_a._new_index_name = lambda: next(names)
    engine_a._request = request
    engine_a._commit_aliases = commit_aliases
    engine_a._index_write_blocked = write_blocked
    engine_a._bulk_to = bulk_to
    engine_a._replay_index = replay

    async def chunks():
        yield replace(_chunk(), mutation_generation=1, acl_tokens=("sid:old",))

    rebuild = asyncio.create_task(engine_a.rebuild(chunks()))
    await initialization_started.wait()
    with pytest.raises(RuntimeError, match="owns the lease"):
        await engine_b.ensure_index()
    allow_initialization.set()
    await block_entered.wait()

    async def ensure_initial():
        return initial

    async def write_newer(_alias, chunks):
        source = engine_b._document_source(chunks)
        stores[initial] = {
            "version": source["mutation_generation"] * 2 + 1,
            "source": source,
        }
        return BulkResult(accepted=len(chunks))

    async def write_tombstone(_alias, mutation):
        stores[initial] = {
            "version": mutation.generation * 2,
            "source": {"record_type": "tombstone"},
        }

    engine_b.ensure_index = ensure_initial
    engine_b._write_tombstone = write_tombstone
    engine_b._bulk_to = write_newer
    await engine_b.bulk_index(
        [replace(_chunk(), mutation_generation=2, acl_tokens=("sid:new",))]
    )
    mutation_finished.set()
    assert await rebuild == candidate
    assert stores[candidate]["version"] == 5
    assert stores[candidate]["source"]["acl_tokens"] == ["sid:new"]
    await engine_a.close()
    await engine_b.close()


@pytest.mark.asyncio
async def test_rebuild_lease_excludes_second_engine_and_releases_with_occ():
    lock = {"source": None, "seq_no": 0, "primary_term": 1}
    delete_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_params
        if request.url.path == f"/{COORDINATION_INDEX}":
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.endswith("/_create/rebuild-lock"):
            if lock["source"] is not None:
                return httpx.Response(409, json={"result": "conflict"})
            lock["source"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={"_seq_no": lock["seq_no"], "_primary_term": lock["primary_term"]},
            )
        if request.method == "GET" and request.url.path.endswith("/_doc/rebuild-lock"):
            return httpx.Response(
                200,
                json={
                    "_source": lock["source"],
                    "_seq_no": lock["seq_no"],
                    "_primary_term": lock["primary_term"],
                },
            )
        if request.method == "DELETE" and request.url.path.endswith(
            "/_doc/rebuild-lock"
        ):
            delete_params = dict(request.url.params)
            lock["source"] = None
            lock["seq_no"] += 1
            return httpx.Response(200, json={"result": "deleted"})
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    client_a = httpx.AsyncClient(base_url="http://127.0.0.1:9200", transport=transport)
    client_b = httpx.AsyncClient(base_url="http://127.0.0.1:9200", transport=transport)
    engine_a = _engine(client=client_a)
    engine_b = _engine(client=client_b)
    lease = await engine_a._acquire_rebuild_lease(ACTIVE_INDEX, "candidate-a")
    assert lock["source"] | {} == {
        "owner": lease[0],
        "created_at": lock["source"]["created_at"],
        "source_index": ACTIVE_INDEX,
        "candidate_index": "candidate-a",
        "phase": "building",
        "source_write_blocked": False,
        "task_id": "",
        "last_verified_alias": ACTIVE_INDEX,
    }
    with pytest.raises(RuntimeError, match="another OpenSearch rebuild"):
        await engine_b._acquire_rebuild_lease(ACTIVE_INDEX, "candidate-b")
    await engine_a._release_rebuild_lease(lease)
    assert delete_params == {
        "if_seq_no": "0",
        "if_primary_term": "1",
        "refresh": "true",
    }
    second = await engine_b._acquire_rebuild_lease(ACTIVE_INDEX, "candidate-b")
    assert second[0] != lease[0]
    await client_a.aclose()
    await client_b.aclose()


@pytest.mark.asyncio
async def test_lost_rebuild_lease_create_ack_is_read_from_document_endpoint():
    source = None
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal source
        paths.append((request.method, request.url.path))
        if request.url.path == f"/{COORDINATION_INDEX}":
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.endswith("/_create/rebuild-lock"):
            source = json.loads(request.content)
            raise httpx.ReadTimeout("lost create response", request=request)
        if request.method == "GET" and request.url.path.endswith("/_doc/rebuild-lock"):
            return httpx.Response(
                200,
                json={"_source": source, "_seq_no": 7, "_primary_term": 3},
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    lease = await engine._acquire_rebuild_lease(ACTIVE_INDEX, "candidate-a")
    assert lease == (source["owner"], 7, 3)
    assert ("GET", f"/{COORDINATION_INDEX}/_doc/rebuild-lock") in paths
    assert (
        "GET",
        f"/{COORDINATION_INDEX}/_create/rebuild-lock",
    ) not in paths
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_lease_phase_update_is_durable_and_uses_current_occ():
    state = {
        "_source": {
            "owner": "owner",
            "created_at": "2026-09-01T00:00:00Z",
            "source_index": ACTIVE_INDEX,
            "candidate_index": "candidate-a",
            "phase": "building",
            "source_write_blocked": False,
            "task_id": "",
            "last_verified_alias": ACTIVE_INDEX,
        },
        "_seq_no": 4,
        "_primary_term": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/_doc/rebuild-lock"):
            return httpx.Response(200, json=state)
        if request.method == "PUT" and request.url.path.endswith("/_doc/rebuild-lock"):
            assert dict(request.url.params) == {
                "if_seq_no": "4",
                "if_primary_term": "2",
                "refresh": "true",
            }
            state["_source"] = json.loads(request.content)
            state["_seq_no"] = 5
            return httpx.Response(200, json={"_seq_no": 5, "_primary_term": 2})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    await engine._update_rebuild_lease(
        ("owner", 0, 1),
        phase="replaying",
        source_write_blocked=True,
        task_id="node:42",
    )
    assert state["_source"]["phase"] == "replaying"
    assert state["_source"]["source_write_blocked"] is True
    assert state["_source"]["task_id"] == "node:42"
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_replay_uses_bounded_async_task_polling():
    calls = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        calls.append(request)
        if request.url.path == "/_reindex":
            return httpx.Response(200, json={"task": "node:42"})
        if request.url.path == "/_tasks/node:42":
            polls += 1
            if polls == 1:
                return httpx.Response(200, json={"completed": False})
            return httpx.Response(
                200,
                json={
                    "completed": True,
                    "response": {
                        "total": 1,
                        "created": 1,
                        "updated": 0,
                        "version_conflicts": 0,
                        "noops": 0,
                        "failures": [],
                        "timed_out": False,
                    },
                },
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(
        client=client, limits=OpenSearchLimits(rebuild_replay_poll_seconds=0)
    )
    result = await engine._replay_index(ACTIVE_INDEX, f"{PHYSICAL_PREFIX}new")
    start = calls[0]
    assert start.url.params["wait_for_completion"] == "false"
    assert json.loads(start.content)["dest"]["version_type"] == "external"
    assert result["created"] == 1 and polls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_ambiguous_rebuild_replay_start_is_never_retried():
    starts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal starts
        if request.url.path == "/_reindex":
            starts += 1
            raise httpx.ReadTimeout("lost start response", request=request)
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    with pytest.raises(RebuildReplayUncertain, match="start outcome is uncertain"):
        await engine._replay_index(ACTIVE_INDEX, f"{PHYSICAL_PREFIX}new")
    assert starts == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_replay_timeout_is_explicitly_quarantined():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_reindex":
            return httpx.Response(200, json={"task": "node:99"})
        if request.url.path == "/_tasks/node:99":
            return httpx.Response(200, json={"completed": False})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(
        client=client,
        limits=OpenSearchLimits(
            rebuild_replay_timeout_seconds=0, rebuild_replay_poll_seconds=0
        ),
    )
    with pytest.raises(RebuildReplayUncertain, match="before quarantine"):
        await engine._replay_index(ACTIVE_INDEX, f"{PHYSICAL_PREFIX}new")
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_replay_task_http_error_is_explicitly_quarantined():
    starts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal starts
        if request.url.path == "/_reindex":
            starts += 1
            return httpx.Response(200, json={"task": "node:404"})
        if request.url.path == "/_tasks/node:404":
            return httpx.Response(404, json={"error": "task_not_found"})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    with pytest.raises(RebuildReplayUncertain, match="task state is uncertain"):
        await engine._replay_index(ACTIVE_INDEX, f"{PHYSICAL_PREFIX}new")
    assert starts == 1
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("quarantine_kind", ["lease", "write_block"])
async def test_search_refuses_rebuild_quarantine(quarantine_kind):
    engine = _engine()

    async def ensure_index():
        return ACTIVE_INDEX

    async def lease_state():
        return (
            {"_source": {"phase": "quarantined"}}
            if quarantine_kind == "lease"
            else None
        )

    async def write_blocked(_index):
        return quarantine_kind == "write_block"

    engine.ensure_index = ensure_index
    engine._rebuild_lease_state = lease_state
    engine._index_write_blocked = write_blocked
    with pytest.raises(SearchUnavailableError, match="rebuild quarantine"):
        await engine.search(SearchRequest(query="contract", acl_tokens=("acl",)))
    await engine.close()


@pytest.mark.asyncio
async def test_uncertain_rebuild_retains_block_candidate_and_owner_lease():
    engine = _engine()
    calls = []
    released = False

    async def active_index():
        return ACTIVE_INDEX

    async def acquire(_source_index, _candidate_index, **_kwargs):
        return ("owner", 0, 1)

    async def update(_lease, **_updates):
        return None

    async def release(_lease):
        nonlocal released
        released = True

    async def request(method, path, **_kwargs):
        calls.append((method, path))
        if path == f"/{ACTIVE_INDEX}/_settings":
            payload = {ACTIVE_INDEX: {"settings": {"index.blocks.write": "false"}}}
        elif path == f"/{ACTIVE_INDEX}/_block/write":
            payload = {"acknowledged": True, "shards_acknowledged": True}
        elif path.endswith("/_refresh"):
            payload = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
        else:
            payload = {}
        return httpx.Response(200, request=httpx.Request(method, path), json=payload)

    async def bulk_to(_index, chunks):
        return BulkResult(accepted=len(chunks))

    async def replay(_source, _destination, _lease):
        raise RebuildReplayUncertain("task still running")

    engine._active_index = active_index
    _stub_rebuild_source(engine)
    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release
    engine._request = request
    engine._bulk_to = bulk_to
    engine._replay_index = replay

    async def chunks():
        yield _chunk()

    with pytest.raises(RebuildReplayUncertain, match="still running"):
        await engine.rebuild(chunks())
    assert not released
    assert not any(method == "DELETE" for method, _path in calls)
    assert not any(
        path.endswith("/_settings") and method == "PUT" for method, path in calls
    )
    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_point", "expected_phase"),
    [
        ("after_replay_complete", "replay_complete"),
        ("after_cutover", "cutover"),
        ("after_alias_commit", "cutover"),
    ],
)
async def test_uncertain_rebuild_preserves_last_proven_phase(
    failure_point, expected_phase
):
    engine = _engine()
    phases = []
    released = False
    alias_committed = False

    _stub_rebuild_source(engine)

    async def active_index():
        return ACTIVE_INDEX

    async def acquire(_source, _candidate, **_kwargs):
        return ("owner", 0, 1)

    async def release(_lease):
        nonlocal released
        released = True

    async def update(_lease, **updates):
        phase = updates.get("phase")
        if failure_point == "after_alias_commit" and phase == "complete":
            raise RebuildReplayUncertain("lease completion is uncertain")
        if phase:
            phases.append(phase)

    async def request(method, path, **_kwargs):
        if path == f"/{ACTIVE_INDEX}/_block/write":
            payload = {"acknowledged": True, "shards_acknowledged": True}
        elif path.endswith("/_refresh"):
            payload = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
        else:
            payload = {}
        return httpx.Response(200, request=httpx.Request(method, path), json=payload)

    async def write_blocked(_index):
        return False

    async def bulk_to(_index, chunks):
        return BulkResult(accepted=len(chunks))

    async def replay(_source, _destination, lease):
        await update(lease, phase="replay_complete")
        if failure_point == "after_replay_complete":
            raise RebuildReplayUncertain("post-replay state is uncertain")
        return {
            "total": 0,
            "created": 0,
            "updated": 0,
            "version_conflicts": 0,
            "noops": 0,
            "failures": [],
            "timed_out": False,
        }

    async def commit_aliases(_index):
        nonlocal alias_committed
        if failure_point == "after_cutover":
            raise RebuildReplayUncertain("alias outcome is uncertain")
        alias_committed = True

    engine._active_index = active_index
    engine._acquire_rebuild_lease = acquire
    engine._release_rebuild_lease = release
    engine._update_rebuild_lease = update
    engine._request = request
    engine._index_write_blocked = write_blocked
    engine._bulk_to = bulk_to
    engine._replay_index = replay
    engine._commit_aliases = commit_aliases

    async def chunks():
        yield _chunk()

    with pytest.raises(RebuildReplayUncertain):
        await engine.rebuild(chunks())
    assert phases[-1] == expected_phase
    assert "quarantined" not in phases
    assert released is False
    if failure_point == "after_alias_commit":
        assert alias_committed is True
    await engine.close()


@pytest.mark.asyncio
async def test_lost_write_block_response_retains_blocking_lease_and_candidate():
    deleted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deleted
        if request.method == "DELETE":
            deleted = True
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    phases = []
    released = False
    unblocked = False
    delayed_block_applied = False

    _stub_rebuild_source(engine)

    async def acquire(_source, _candidate, **_kwargs):
        return ("owner", 0, 1)

    async def update(_lease, **updates):
        if updates.get("phase"):
            phases.append(updates["phase"])

    async def release(_lease):
        nonlocal released
        released = True

    async def request(method, path, **_kwargs):
        nonlocal delayed_block_applied
        if path == f"/{ACTIVE_INDEX}/_block/write":
            asyncio.get_running_loop().call_soon(lambda: set_delayed_block_applied())
            raise httpx.ReadTimeout("write-block response lost")
        if path.endswith("/_refresh"):
            payload = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
        else:
            payload = {}
        return httpx.Response(200, request=httpx.Request(method, path), json=payload)

    def set_delayed_block_applied():
        nonlocal delayed_block_applied
        delayed_block_applied = True

    async def write_blocked(_index):
        return False

    async def bulk_to(_index, chunks):
        return BulkResult(accepted=len(chunks))

    async def unblock(_index):
        nonlocal unblocked
        unblocked = True

    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release
    engine._request = request
    engine._index_write_blocked = write_blocked
    engine._bulk_to = bulk_to
    engine._unblock_index_writes = unblock

    async def chunks():
        yield _chunk()

    with pytest.raises(RebuildReplayUncertain, match="block outcome is uncertain"):
        await engine.rebuild(chunks())
    await asyncio.sleep(0)
    assert delayed_block_applied is True
    assert phases[-1] == "blocking"
    assert released is False
    assert unblocked is False
    assert deleted is False
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transition", "expected_phase"),
    [
        ("block", "blocking"),
        ("reindex_start", "blocked"),
        ("task_poll", "replaying"),
        ("alias", "cutover"),
    ],
)
async def test_non_object_transition_response_retains_rebuild_quarantine(
    transition, expected_phase
):
    deleted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deleted
        if request.method == "DELETE":
            deleted = True
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(
        client=client, limits=OpenSearchLimits(rebuild_replay_poll_seconds=0)
    )
    phases = []
    released = False
    unblocked = False
    active_reads = 0

    _stub_rebuild_source(engine)

    async def active_index():
        nonlocal active_reads
        active_reads += 1
        if active_reads == 1:
            return ACTIVE_INDEX
        raise httpx.ReadTimeout("alias reconciliation unavailable")

    async def acquire(_source, _candidate, **_kwargs):
        return ("owner", 0, 1)

    async def update(_lease, **updates):
        if updates.get("phase"):
            phases.append(updates["phase"])

    async def release(_lease):
        nonlocal released
        released = True

    async def request(method, path, **_kwargs):
        request = httpx.Request(method, path)
        if path == f"/{ACTIVE_INDEX}/_block/write":
            payload = (
                []
                if transition == "block"
                else {"acknowledged": True, "shards_acknowledged": True}
            )
        elif path == "/_reindex":
            payload = None if transition == "reindex_start" else {"task": "node:1"}
        elif path == "/_tasks/node:1":
            payload = (
                []
                if transition == "task_poll"
                else {
                    "completed": True,
                    "response": {
                        "total": 0,
                        "created": 0,
                        "updated": 0,
                        "version_conflicts": 0,
                        "noops": 0,
                        "failures": [],
                        "timed_out": False,
                    },
                }
            )
        elif path == "/_aliases":
            payload = [] if transition == "alias" else {"acknowledged": True}
        elif path.endswith("/_refresh"):
            payload = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
        else:
            payload = {}
        return httpx.Response(200, request=request, json=payload)

    async def write_blocked(_index):
        return False

    async def bulk_to(_index, chunks):
        return BulkResult(accepted=len(chunks))

    async def unblock(_index):
        nonlocal unblocked
        unblocked = True

    engine._active_index = active_index
    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release
    engine._request = request
    engine._index_write_blocked = write_blocked
    engine._bulk_to = bulk_to
    engine._unblock_index_writes = unblock

    async def chunks():
        yield _chunk()

    with pytest.raises(RebuildReplayUncertain):
        await engine.rebuild(chunks())
    assert phases[-1] == expected_phase
    assert released is False
    assert unblocked is False
    assert deleted is False
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_retains_block_and_cutover_lease_when_alias_outcome_is_unknown():
    engine = _engine()
    phases = []
    released = False
    unblocked = False
    active_reads = 0

    _stub_rebuild_source(engine)

    async def active_index():
        nonlocal active_reads
        active_reads += 1
        if active_reads == 1:
            return ACTIVE_INDEX
        raise httpx.ReadTimeout("alias reconciliation unavailable")

    async def acquire(_source, _candidate, **_kwargs):
        return ("owner", 0, 1)

    async def update(_lease, **updates):
        if updates.get("phase"):
            phases.append(updates["phase"])

    async def release(_lease):
        nonlocal released
        released = True

    async def request(method, path, **_kwargs):
        if path == f"/{ACTIVE_INDEX}/_block/write":
            payload = {"acknowledged": True, "shards_acknowledged": True}
        elif path.endswith("/_refresh"):
            payload = {"_shards": {"total": 1, "successful": 1, "failed": 0}}
        else:
            payload = {}
        return httpx.Response(200, request=httpx.Request(method, path), json=payload)

    async def write_blocked(_index):
        return False

    async def bulk_to(_index, chunks):
        return BulkResult(accepted=len(chunks))

    async def replay(_source, _destination, _lease):
        return {
            "total": 0,
            "created": 0,
            "updated": 0,
            "version_conflicts": 0,
            "noops": 0,
            "failures": [],
            "timed_out": False,
        }

    async def swap_aliases(_index):
        raise httpx.ReadTimeout("alias response lost")

    async def unblock(_index):
        nonlocal unblocked
        unblocked = True

    engine._active_index = active_index
    engine._acquire_rebuild_lease = acquire
    engine._update_rebuild_lease = update
    engine._release_rebuild_lease = release
    engine._request = request
    engine._index_write_blocked = write_blocked
    engine._bulk_to = bulk_to
    engine._replay_index = replay
    engine._swap_aliases = swap_aliases
    engine._unblock_index_writes = unblock

    async def chunks():
        yield _chunk()

    with pytest.raises(RebuildReplayUncertain, match="outcome could not be verified"):
        await engine.rebuild(chunks())
    assert phases[-1] == "cutover"
    assert released is False
    assert unblocked is False
    await engine.close()


@pytest.mark.asyncio
async def test_rebuild_rejects_multiple_versions_of_one_document():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.startswith("/_alias/"):
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(404)
        if request.method == "DELETE":
            return httpx.Response(200, json={"acknowledged": True})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    _stub_rebuild_lease(engine)
    _stub_rebuild_source(engine)

    async def chunks():
        yield replace(_chunk(1), document_version="v1")
        yield replace(_chunk(2), document_version="v2")

    with pytest.raises(ValueError, match="multiple versions"):
        await engine.rebuild(chunks())
    await client.aclose()


@pytest.mark.asyncio
async def test_rebuild_requires_document_order_for_constant_memory_validation():
    engine = _engine()
    _stub_rebuild_lease(engine)
    _stub_rebuild_source(engine)

    async def request(method, path, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request(method, f"http://127.0.0.1:9200{path}"),
            json={"acknowledged": True},
        )

    engine._request = request

    async def active_index():
        return None

    engine._active_index = active_index

    async def chunks():
        yield replace(_chunk(), document_id="doc-2", chunk_id="doc-2:1")
        yield replace(_chunk(), document_id="doc-1", chunk_id="doc-1:1")

    with pytest.raises(ValueError, match="ordered by document_id"):
        await engine.rebuild(chunks())
    await engine.close()


@pytest.mark.asyncio
async def test_rebuild_rejects_oversized_document_before_accumulating_rest():
    engine = _engine(limits=OpenSearchLimits(max_document_chunks=2))
    _stub_rebuild_lease(engine)
    _stub_rebuild_source(engine)
    yielded = 0

    async def active_index():
        return None

    async def request(method, path, **_kwargs):
        return httpx.Response(200, request=httpx.Request(method, path), json={})

    engine._active_index = active_index
    engine._request = request

    async def chunks():
        nonlocal yielded
        for number in range(10):
            yielded += 1
            yield _chunk(number)

    with pytest.raises(ValueError, match="one rebuild document exceeds"):
        await engine.rebuild(chunks())
    assert yielded == 3
    await engine.close()


@pytest.mark.asyncio
async def test_one_document_may_hold_more_chunks_than_a_bulk_batch():
    """A 2,000-page PDF is one document; the batch size must not cap its chunks."""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            bodies.append(request.content)
            return httpx.Response(200, json={"items": [{"index": {"status": 201}}]})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    # Two documents per bulk request, but up to fifty chunks in one document.
    engine = _engine(
        client=client,
        limits=OpenSearchLimits(max_bulk_documents=2, max_document_chunks=50),
    )
    result = await engine._bulk_to(
        WRITE_ALIAS, [_chunk(number) for number in range(10)]
    )
    assert result.accepted == 10 and result.failed_ids == ()
    # One document, therefore one bulk action carrying all ten chunks.
    assert len(bodies) == 1
    action, source = [json.loads(line) for line in bodies[0].splitlines()]
    assert action["index"]["_index"] == WRITE_ALIAS
    assert len(source["chunks"]) == 10
    await client.aclose()


@pytest.mark.asyncio
async def test_a_document_over_the_chunk_ceiling_fails_without_its_neighbours():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            return httpx.Response(200, json={"items": [{"index": {"status": 201}}]})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client, limits=OpenSearchLimits(max_document_chunks=2))
    oversized = [replace(_chunk(number), document_id="doc-2") for number in range(5)]
    result = await engine._bulk_to(WRITE_ALIAS, [_chunk(1)] + oversized)
    # The healthy neighbour still lands; only the oversized document fails.
    assert result.accepted == 1
    assert set(result.failed_ids) == {chunk.chunk_id for chunk in oversized}
    await client.aclose()


@pytest.mark.asyncio
async def test_a_document_larger_than_the_batch_budget_still_indexes():
    """The 8 MiB transport budget must not cap the 20 MiB extraction budget."""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            bodies.append(request.content)
            return httpx.Response(200, json={"items": [{"index": {"status": 201}}]})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(
        client=client,
        limits=OpenSearchLimits(max_bulk_bytes=4_096, max_document_bytes=1_000_000),
    )
    big = replace(_chunk(1), content="x" * 50_000)
    result = await engine._bulk_to(WRITE_ALIAS, [big])
    assert result.accepted == 1 and result.failed_ids == ()
    assert len(bodies) == 1 and len(bodies[0]) > 4_096

    # Past its own ceiling the document does fail, and says so as a document.
    engine.limits = OpenSearchLimits(max_bulk_bytes=4_096, max_document_bytes=1_000)
    refused = await engine._bulk_to(WRITE_ALIAS, [big])
    assert refused.accepted == 0 and refused.failed_ids == (big.chunk_id,)
    await client.aclose()


@pytest.mark.asyncio
async def test_batches_split_on_the_byte_budget_rather_than_failing():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            bodies.append(request.content)
            count = len(request.content.decode().strip().splitlines()) // 2
            return httpx.Response(
                200, json={"items": [{"index": {"status": 201}} for _ in range(count)]}
            )
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(
        client=client,
        limits=OpenSearchLimits(max_bulk_documents=100, max_bulk_bytes=3_000),
    )
    documents = [
        replace(
            _chunk(1),
            document_id=f"doc-{number}",
            chunk_id=f"doc-{number}:1",
            content="y" * 800,
        )
        for number in range(6)
    ]
    result = await engine._bulk_to(WRITE_ALIAS, documents)
    assert result.accepted == 6 and result.failed_ids == ()
    assert len(bodies) > 1, "the batch should have been split, not rejected"
    assert all(len(body) <= 3_000 for body in bodies)
    await client.aclose()


@pytest.mark.asyncio
async def test_stale_mutation_generation_is_rejected_before_content_write():
    active_version = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_version
        if request.method == "PUT" and "/_doc/" in request.url.path:
            version = int(request.url.params["version"])
            if version <= active_version:
                return httpx.Response(409, json={"result": "conflict"})
            active_version = version
            return httpx.Response(201, json={"result": "updated"})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    writes = []

    async def ensure_index():
        return ACTIVE_INDEX

    async def bulk_to(_alias, chunks):
        writes.extend(chunk.mutation_generation for chunk in chunks)
        return BulkResult(accepted=len(chunks))

    engine.ensure_index = ensure_index
    engine._bulk_to = bulk_to
    await engine.bulk_index([replace(_chunk(), mutation_generation=2)])
    with pytest.raises(RuntimeError, match="stale"):
        await engine.bulk_index([replace(_chunk(), mutation_generation=1)])
    assert active_version == 4 and writes == [2]
    await client.aclose()


@pytest.mark.asyncio
async def test_delayed_older_worker_cannot_overwrite_newer_document_envelope():
    state = {"version": 0, "source": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            action, source = [json.loads(line) for line in request.content.splitlines()]
            version = int(action["index"]["version"])
            if version <= state["version"]:
                return httpx.Response(200, json={"items": [{"index": {"status": 409}}]})
            state.update(version=version, source=source)
            return httpx.Response(200, json={"items": [{"index": {"status": 201}}]})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    newer = replace(_chunk(), mutation_generation=2, acl_tokens=("sid:new",))
    older = replace(_chunk(), mutation_generation=1, acl_tokens=("sid:old",))
    assert (await engine._bulk_to(engine.write_alias, [newer])).accepted == 1
    delayed = await engine._bulk_to(engine.write_alias, [older])
    assert delayed.failed_ids == ("doc-1:1",)
    assert state["version"] == 5
    assert state["source"]["acl_tokens"] == ["sid:new"]
    await client.aclose()


@pytest.mark.asyncio
async def test_uncertain_atomic_document_write_is_verified_by_digest():
    stored = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            _action, source = [
                json.loads(line) for line in request.content.splitlines()
            ]
            stored.update(source)
            raise httpx.ReadTimeout("response lost", request=request)
        if request.method == "GET" and "/_doc/" in request.url.path:
            return httpx.Response(200, json={"_source": stored})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    result = await engine._bulk_to(engine.write_alias, [_chunk()])
    assert result.accepted == 1 and result.failed_ids == ()
    await client.aclose()


@pytest.mark.asyncio
async def test_uncertain_write_rejects_same_content_with_different_acl():
    stored = {}
    engine = None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            assert engine is not None
            stored.update(
                engine._document_source(
                    [replace(_chunk(), acl_tokens=("sid:competing",))]
                )
            )
            raise httpx.ReadTimeout("response lost", request=request)
        if request.method == "GET" and "/_doc/" in request.url.path:
            return httpx.Response(200, json={"_source": stored})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    with pytest.raises(RuntimeError, match="could not be verified"):
        await engine._bulk_to(engine.write_alias, [_chunk()])
    await client.aclose()


@pytest.mark.asyncio
async def test_uncertain_tombstone_is_verified_before_returning():
    stored = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            stored.update(json.loads(request.content))
            raise httpx.ReadTimeout("response lost", request=request)
        if request.method == "GET":
            return httpx.Response(200, json={"_source": stored})
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9200", transport=httpx.MockTransport(handler)
    )
    engine = _engine(client=client)
    await engine._write_tombstone(engine.write_alias, DocumentMutation("doc-1", 3))
    assert stored["record_type"] == "tombstone"
    assert stored["mutation_generation"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_restore_is_prefix_scoped_alias_free_and_synchronous():
    engine = _engine()
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/_snapshot/"):
            payload = {
                "snapshots": [
                    {
                        "state": "SUCCESS",
                        "indices": [f"{PHYSICAL_PREFIX}restored"],
                        "shards": {"total": 1, "successful": 1, "failed": 0},
                    }
                ]
            }
        elif method == "POST":
            payload = {"accepted": True}
        else:
            payload = {
                f"{PHYSICAL_PREFIX}restored": {
                    "shards": [
                        {
                            "id": 0,
                            "primary": True,
                            "stage": "DONE",
                            "type": "SNAPSHOT",
                            "source": {"repository": "repo", "snapshot": "snapshot"},
                        }
                    ]
                }
            }
        return httpx.Response(
            200,
            request=httpx.Request(method, "http://127.0.0.1"),
            json=payload,
        )

    engine._request = request
    result = await engine.restore_snapshot("repo", "snapshot")
    restore = next(call for call in calls if call[0] == "POST")
    assert restore[2]["params"] == {"wait_for_completion": "false"}
    assert restore[2]["json"] == {
        "indices": f"{PHYSICAL_PREFIX}restored",
        "include_aliases": False,
        "include_global_state": False,
    }
    assert calls[-1][1].endswith("/_recovery")
    assert result["state"] == "SUCCESS"
    await engine.close()


@pytest.mark.asyncio
async def test_snapshot_restore_rejects_malformed_success_metadata():
    engine = _engine()

    async def request(method, _path, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request(method, "http://127.0.0.1"),
            json={"snapshots": [{"state": "SUCCESS", "indices": [], "shards": {}}]},
        )

    engine._request = request
    with pytest.raises(RuntimeError, match="not exactly restorable"):
        await engine.restore_snapshot("repo", "snapshot")
    await engine.close()


@pytest.mark.asyncio
async def test_snapshot_restore_holds_until_exact_recovery_or_timeout():
    engine = _engine(
        limits=OpenSearchLimits(restore_timeout_seconds=0, restore_poll_seconds=0)
    )

    async def request(method, path, **_kwargs):
        if method == "GET" and path.startswith("/_snapshot/"):
            payload = {
                "snapshots": [
                    {
                        "state": "SUCCESS",
                        "indices": [f"{PHYSICAL_PREFIX}restored"],
                        "shards": {"total": 1, "successful": 1, "failed": 0},
                    }
                ]
            }
        elif method == "POST":
            payload = {"accepted": True}
        else:
            payload = {
                f"{PHYSICAL_PREFIX}restored": {
                    "shards": [
                        {
                            "id": 0,
                            "primary": True,
                            "stage": "INDEX",
                            "type": "SNAPSHOT",
                            "source": {"repository": "repo", "snapshot": "snapshot"},
                        }
                    ]
                }
            }
        return httpx.Response(
            200,
            request=httpx.Request(method, "http://127.0.0.1"),
            json=payload,
        )

    engine._request = request
    with pytest.raises(TimeoutError, match="did not complete"):
        await engine.restore_snapshot("repo", "snapshot")
    await engine.close()


@pytest.mark.asyncio
async def test_snapshot_restore_recovers_after_lost_acceptance_response():
    engine = _engine()

    async def request(method, path, **_kwargs):
        if method == "GET" and path.startswith("/_snapshot/"):
            payload = {
                "snapshots": [
                    {
                        "state": "SUCCESS",
                        "indices": [f"{PHYSICAL_PREFIX}restored"],
                        "shards": {"total": 1, "successful": 1, "failed": 0},
                    }
                ]
            }
        elif method == "POST":
            raise httpx.ReadTimeout("response lost")
        else:
            payload = {
                f"{PHYSICAL_PREFIX}restored": {
                    "shards": [
                        {
                            "id": 0,
                            "primary": True,
                            "stage": "DONE",
                            "type": "SNAPSHOT",
                            "source": {"repository": "repo", "snapshot": "snapshot"},
                        }
                    ]
                }
            }
        return httpx.Response(
            200,
            request=httpx.Request(method, "http://127.0.0.1"),
            json=payload,
        )

    engine._request = request
    result = await engine.restore_snapshot("repo", "snapshot")
    assert result["state"] == "SUCCESS"
    await engine.close()


@pytest.mark.asyncio
async def test_snapshot_restore_rejects_wrong_or_incomplete_primary_shard_set():
    engine = _engine(
        limits=OpenSearchLimits(restore_timeout_seconds=0, restore_poll_seconds=0)
    )

    async def request(method, path, **_kwargs):
        if method == "GET" and path.startswith("/_snapshot/"):
            payload = {
                "snapshots": [
                    {
                        "state": "SUCCESS",
                        "indices": [f"{PHYSICAL_PREFIX}restored"],
                        "shards": {"total": 3, "successful": 3, "failed": 0},
                    }
                ]
            }
        elif method == "POST":
            payload = {"accepted": True}
        else:
            payload = {
                f"{PHYSICAL_PREFIX}restored": {
                    "shards": [
                        {
                            "id": shard_id,
                            "primary": True,
                            "stage": "DONE",
                            "type": "SNAPSHOT",
                            "source": {
                                "repository": "repo",
                                "snapshot": "different-snapshot"
                                if shard_id == 1
                                else "snapshot",
                            },
                        }
                        for shard_id in range(2)
                    ]
                }
            }
        return httpx.Response(
            200,
            request=httpx.Request(method, "http://127.0.0.1"),
            json=payload,
        )

    engine._request = request
    with pytest.raises(TimeoutError, match="did not complete"):
        await engine.restore_snapshot("repo", "snapshot")
    await engine.close()


def test_folder_scopes_are_paired_with_share_and_bounded():
    engine = _engine()
    request = SearchRequest(
        "notice",
        ("sid",),
        filters=SearchFilters(path_scopes=(("a", "root-a"), ("b", "root-b"))),
    )
    body = engine._search_body(request)
    scopes = body["query"]["bool"]["filter"][2]["bool"]["should"]
    assert scopes[0]["bool"]["filter"][0] == {"term": {"share_id": "a"}}
    assert (
        scopes[0]["bool"]["filter"][1]["prefix"]["relative_path"]["case_insensitive"]
        is True
    )
    assert scopes[1]["bool"]["filter"][0] == {"term": {"share_id": "b"}}
    with pytest.raises(ValueError, match="path scopes"):
        engine._search_body(
            replace(request, filters=SearchFilters(path_scopes=(("a", "root"),) * 101))
        )

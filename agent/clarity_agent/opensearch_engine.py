"""OpenSearch-backed local Search Node serving engine."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import ssl
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from clarity_agent.search_engine import (
    INDEX_SCHEMA_VERSION,
    BulkResult,
    DocumentChunk,
    DocumentMutation,
    EngineHealth,
    LocalSearchEngine,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

DEFAULT_MAX_RESULTS = 100
DEFAULT_MAX_OFFSET = 10_000
DEFAULT_MAX_QUERY_CHARS = 1_000
DEFAULT_MAX_BULK_DOCUMENTS = 500
DEFAULT_MAX_BULK_BYTES = 8 * 1024 * 1024
# One document is one atomic envelope, so a per-document ceiling cannot be the
# batch ceiling. A 2,000-page PDF legitimately produces thousands of chunks, and
# the extraction workers' own default output budget is 20 MiB per document.
DEFAULT_MAX_DOCUMENT_CHUNKS = 5_000
DEFAULT_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class RebuildReplayUncertain(RuntimeError):
    """Replay may still be running; retain the block, candidate, and lease."""


class SearchUnavailableError(RuntimeError):
    """Search is fail-closed while rebuild state is quarantined."""


def require_loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("OpenSearch URL must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("OpenSearch credentials must not be embedded in the URL")
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port)
        }
    except OSError as exc:
        raise ValueError("OpenSearch hostname could not be resolved") from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_loopback for address in addresses
    ):
        raise ValueError("OpenSearch must be reachable only through a loopback URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class OpenSearchLimits:
    max_results: int = DEFAULT_MAX_RESULTS
    max_offset: int = DEFAULT_MAX_OFFSET
    max_query_chars: int = DEFAULT_MAX_QUERY_CHARS
    max_bulk_documents: int = DEFAULT_MAX_BULK_DOCUMENTS
    max_bulk_bytes: int = DEFAULT_MAX_BULK_BYTES
    max_document_chunks: int = DEFAULT_MAX_DOCUMENT_CHUNKS
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES
    request_timeout_seconds: float = 10.0
    disk_watermark_low: str = "80%"
    disk_watermark_high: str = "90%"
    disk_watermark_flood: str = "95%"
    restore_timeout_seconds: float = 3_600.0
    restore_poll_seconds: float = 1.0
    rebuild_replay_timeout_seconds: float = 3_600.0
    rebuild_replay_poll_seconds: float = 1.0


class OpenSearchEngine(LocalSearchEngine):
    def __init__(
        self,
        url: str = "http://127.0.0.1:9200",
        *,
        index_prefix: str = "lawhand-firm-memory",
        username: str | None = None,
        password: str | None = None,
        ca_path: str | None = None,
        limits: OpenSearchLimits | None = None,
        client: httpx.AsyncClient | None = None,
        allow_insecure: bool = False,
    ):
        self.url = require_loopback_url(url)
        if urlparse(self.url).scheme != "https" and not allow_insecure:
            raise ValueError("OpenSearch requires HTTPS outside explicit tests")
        if bool(username) != bool(password):
            raise ValueError(
                "OpenSearch username and password must be configured together"
            )
        if client is None and not username and not allow_insecure:
            raise ValueError("OpenSearch requires authenticated access")
        if not index_prefix.replace("-", "").isalnum():
            raise ValueError(
                "index prefix may contain only letters, digits, and hyphens"
            )
        self.index_prefix = index_prefix.lower()
        self.read_alias = f"{self.index_prefix}-read-v{INDEX_SCHEMA_VERSION}"
        self.write_alias = f"{self.index_prefix}-write-v{INDEX_SCHEMA_VERSION}"
        self.coordination_index = (
            f"{self.index_prefix}-coordination-v{INDEX_SCHEMA_VERSION}"
        )
        self.limits = limits or OpenSearchLimits()
        self._mutation_lock = asyncio.Lock()
        auth = (username, password) if username and password else None
        self._owns_client = client is None
        verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=ca_path) if ca_path else True
        )
        self._client = client or httpx.AsyncClient(
            base_url=self.url,
            auth=auth,
            verify=verify,
            timeout=self.limits.request_timeout_seconds,
            trust_env=False,
        )

    @property
    def capabilities(self) -> dict[str, object]:
        return {
            "engine": "opensearch",
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "lexical_ranking": "BM25",
            "phrases": True,
            "boolean_queries": True,
            "field_filters": True,
            "highlighting": True,
            "page_provenance": True,
            "acl_filtering": True,
            "vectors": False,
            "max_results": self.limits.max_results,
            "max_offset": self.limits.max_offset,
        }

    def _mapping(self) -> dict:
        return {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "5s",
                    "mapping.total_fields.limit": 256,
                    "max_result_window": self.limits.max_offset
                    + self.limits.max_results,
                },
                "analysis": {"analyzer": {"lawhand_legal": {"type": "standard"}}},
                "similarity": {"default": {"type": "BM25", "k1": 1.2, "b": 0.75}},
            },
            "mappings": {
                "dynamic": "strict",
                "_meta": {
                    "lawhand_schema_version": INDEX_SCHEMA_VERSION,
                    "content_location": "customer_infrastructure_only",
                },
                "properties": {
                    "document_id": {"type": "keyword"},
                    "record_type": {"type": "keyword"},
                    "mutation_generation": {"type": "long"},
                    "share_id": {"type": "keyword"},
                    "relative_path": {"type": "keyword"},
                    "filename": {
                        "type": "text",
                        "analyzer": "lawhand_legal",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "extension": {"type": "keyword"},
                    "content_hash": {"type": "keyword", "index": False},
                    "document_version": {"type": "keyword"},
                    "modified_at": {"type": "date"},
                    "matter_ids": {"type": "keyword"},
                    "acl_tokens": {"type": "keyword"},
                    "deny_acl_tokens": {"type": "keyword"},
                    "mutation_digest": {"type": "keyword", "index": False},
                    "chunks": {
                        "type": "nested",
                        "properties": {
                            "chunk_id": {"type": "keyword"},
                            "filename": {
                                "type": "text",
                                "analyzer": "lawhand_legal",
                            },
                            "content": {
                                "type": "text",
                                "analyzer": "lawhand_legal",
                            },
                            "page_number": {"type": "integer"},
                            "section_path": {"type": "keyword"},
                            "section_text": {
                                "type": "text",
                                "analyzer": "lawhand_legal",
                            },
                            "ordinal": {"type": "integer"},
                            "start_offset": {"type": "integer", "index": False},
                            "end_offset": {"type": "integer", "index": False},
                            "metadata": {"type": "object", "enabled": False},
                        },
                    },
                    "schema_version": {"type": "integer"},
                },
            },
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    async def _active_index(self) -> str | None:
        response = await self._client.get(
            f"/_alias/{self.read_alias},{self.write_alias}"
        )
        if response.status_code == 404:
            # Distinguish a fresh node from a corrupted one-alias state. These
            # HEADs are used only on the exceptional 404 path.
            read_exists = (
                await self._client.head(f"/_alias/{self.read_alias}")
            ).status_code != 404
            write_exists = (
                await self._client.head(f"/_alias/{self.write_alias}")
            ).status_code != 404
            if read_exists or write_exists:
                raise RuntimeError("OpenSearch read/write aliases are incomplete")
            return None
        response.raise_for_status()
        indexes = response.json()
        if len(indexes) != 1:
            raise RuntimeError("OpenSearch aliases must resolve to one active index")
        index, payload = next(iter(indexes.items()))
        physical_prefix = f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-"
        if not index.startswith(physical_prefix) or index == physical_prefix:
            raise RuntimeError("OpenSearch aliases target an unexpected index")
        aliases = payload.get("aliases", {})
        if set(aliases) != {self.read_alias, self.write_alias}:
            raise RuntimeError("OpenSearch read/write aliases are incomplete")
        if aliases[self.read_alias] != {} or aliases[self.write_alias] != {
            "is_write_index": True
        }:
            raise RuntimeError("OpenSearch aliases have unsafe routing or filters")
        return index

    async def ensure_index(self) -> str:
        current = await self._active_index()
        if current:
            await self._validate_active_mapping(current)
            return current
        index = self._new_index_name()
        lease = await self._acquire_rebuild_lease(None, index, phase="initializing")
        created = False
        try:
            # A cooperating initializer may have completed between the first
            # alias read and our lease acquisition. The shared lease makes this
            # recheck authoritative for all Search Node processes.
            current = await self._active_index()
            if current:
                await self._validate_active_mapping(current)
                await self._release_rebuild_lease(lease)
                return current
            await self._request("PUT", f"/{index}", json=self._mapping())
            created = True
            await self._update_rebuild_lease(
                lease, phase="cutover", last_verified_alias=""
            )
            await self._commit_aliases(index)
            await self._update_rebuild_lease(
                lease, phase="complete", last_verified_alias=index
            )
            await self._release_rebuild_lease(lease)
            return index
        except RebuildReplayUncertain:
            # Preserve the last successfully recorded phase for recovery.
            raise
        except Exception:
            if created:
                try:
                    if await self._active_index() != index:
                        await self._client.delete(f"/{index}")
                except Exception:
                    pass
            try:
                await self._release_rebuild_lease(lease)
            except Exception as release_error:
                raise RebuildReplayUncertain(
                    "OpenSearch initialization lease release is uncertain"
                ) from release_error
            raise

    async def _validate_active_mapping(self, index: str) -> None:
        mapping = (await self._request("GET", f"/{index}/_mapping")).json()
        version = (
            mapping.get(index, {})
            .get("mappings", {})
            .get("_meta", {})
            .get("lawhand_schema_version")
        )
        if version != INDEX_SCHEMA_VERSION:
            raise RuntimeError("active OpenSearch index schema is incompatible")

    def _new_index_name(self) -> str:
        return f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"

    async def _swap_aliases(self, index: str) -> dict:
        result = (
            await self._request(
                "POST",
                "/_aliases",
                json={
                    "actions": [
                        {
                            "remove": {
                                "index": f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-*",
                                "alias": self.read_alias,
                                "must_exist": False,
                            }
                        },
                        {
                            "remove": {
                                "index": f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-*",
                                "alias": self.write_alias,
                                "must_exist": False,
                            }
                        },
                        {"add": {"index": index, "alias": self.read_alias}},
                        {
                            "add": {
                                "index": index,
                                "alias": self.write_alias,
                                "is_write_index": True,
                            }
                        },
                    ]
                },
            )
        ).json()
        if not isinstance(result, dict):
            raise ValueError("OpenSearch alias response was not an object")
        return result

    async def _commit_aliases(self, index: str) -> None:
        try:
            result = await self._swap_aliases(index)
        except Exception as swap_error:
            # The atomic action can commit even when its response is lost. Never
            # delete a generation until alias state proves it is inactive.
            try:
                active = await self._active_index()
            except Exception as state_error:
                raise RebuildReplayUncertain(
                    "OpenSearch alias commit outcome could not be verified"
                ) from state_error
            if active == index:
                return
            # Even an old alias observed immediately after a lost response does
            # not prove the server-side atomic action cannot commit later.
            raise RebuildReplayUncertain(
                "OpenSearch alias commit did not have a terminal outcome"
            ) from swap_error
        if result.get("acknowledged") is not True:
            raise RebuildReplayUncertain("OpenSearch alias commit was not acknowledged")
        try:
            active = await self._active_index()
        except Exception as state_error:
            raise RebuildReplayUncertain(
                "OpenSearch acknowledged alias commit could not be verified"
            ) from state_error
        if active != index:
            raise RebuildReplayUncertain(
                "OpenSearch alias commit did not activate the new index"
            )

    @staticmethod
    def _chunk_source(chunk: DocumentChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "filename": chunk.filename,
            "content": chunk.content,
            "page_number": chunk.page_number,
            "section_path": list(chunk.section_path),
            "section_text": " > ".join(chunk.section_path),
            "ordinal": chunk.ordinal,
            "start_offset": chunk.start_offset,
            "end_offset": chunk.end_offset,
            "metadata": chunk.metadata,
        }

    @classmethod
    def _document_source(cls, chunks: Sequence[DocumentChunk]) -> dict:
        if not chunks:
            raise ValueError("a document mutation requires at least one chunk")
        first = chunks[0]
        shared = {
            (
                chunk.document_id,
                chunk.share_id,
                chunk.relative_path,
                chunk.filename,
                chunk.extension.lower(),
                chunk.content_hash,
                chunk.document_version or chunk.content_hash,
                chunk.modified_at,
                chunk.mutation_generation,
                chunk.matter_ids,
                chunk.acl_tokens,
                chunk.deny_acl_tokens,
            )
            for chunk in chunks
        }
        if len(shared) != 1:
            raise ValueError(
                "all chunks for a document must share metadata and generation"
            )
        nested = [cls._chunk_source(chunk) for chunk in chunks]
        source = {
            "record_type": "document",
            "document_id": first.document_id,
            "mutation_generation": first.mutation_generation,
            "share_id": first.share_id,
            "relative_path": first.relative_path,
            "filename": first.filename,
            "extension": first.extension.lower(),
            "content_hash": first.content_hash,
            "document_version": first.document_version or first.content_hash,
            "modified_at": first.modified_at.isoformat(),
            "matter_ids": list(first.matter_ids),
            "acl_tokens": list(first.acl_tokens),
            "deny_acl_tokens": list(first.deny_acl_tokens),
            "chunks": nested,
            "schema_version": INDEX_SCHEMA_VERSION,
        }
        source["mutation_digest"] = hashlib.sha256(
            json.dumps(
                source, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        return source

    @staticmethod
    def _document_id(document_id: str) -> str:
        return hashlib.sha256(document_id.encode()).hexdigest()

    @staticmethod
    def _group_by_document(
        chunks: Sequence[DocumentChunk],
    ) -> dict[str, list[DocumentChunk]]:
        grouped: dict[str, list[DocumentChunk]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.document_id, []).append(chunk)
        return grouped

    def _encode_document(self, chunks: Sequence[DocumentChunk]) -> tuple[dict, bytes]:
        """Serialize one atomic document envelope.

        Raises ValueError when the document itself is oversized. That is a
        property of the document, not of the batch it happens to travel in, so
        it must not be measured against the batch limits.
        """
        if len(chunks) > self.limits.max_document_chunks:
            raise ValueError("document exceeds the configured chunk limit")
        source = self._document_source(chunks)
        encoded = json.dumps(source, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > self.limits.max_document_bytes:
            raise ValueError("document exceeds the configured byte limit")
        return source, encoded

    def _index_action(self, alias: str, source: dict) -> bytes:
        return json.dumps(
            {
                "index": {
                    "_index": alias,
                    "_id": self._document_id(source["document_id"]),
                    "version": source["mutation_generation"] * 2 + 1,
                    "version_type": "external",
                }
            },
            separators=(",", ":"),
        ).encode()

    def _batches(
        self, alias: str, documents: Sequence[tuple[dict, bytes]]
    ) -> list[list[tuple[dict, bytes]]]:
        """Pack encoded documents into requests under the batch limits.

        A document that alone exceeds the batch byte budget still travels in a
        batch of its own: it already passed the per-document ceiling, and
        refusing it here would make an 8 MiB transport limit silently cap what
        the 20 MiB extraction budget is allowed to produce.
        """
        batches: list[list[tuple[dict, bytes]]] = []
        current: list[tuple[dict, bytes]] = []
        size = 0
        for entry in documents:
            # Measure what actually goes on the wire: the action line and both
            # newlines count against the request budget, not just the source.
            payload = len(self._index_action(alias, entry[0])) + len(entry[1]) + 2
            if current and (
                len(current) >= self.limits.max_bulk_documents
                or size + payload > self.limits.max_bulk_bytes
            ):
                batches.append(current)
                current, size = [], 0
            current.append(entry)
            size += payload
        if current:
            batches.append(current)
        return batches

    async def _send_batch(
        self, alias: str, batch: Sequence[tuple[dict, bytes]]
    ) -> set[str]:
        """Issue one bulk request; return the document ids that did not land."""
        documents = [source for source, _ in batch]
        body = b"".join(
            self._index_action(alias, source) + b"\n" + encoded + b"\n"
            for source, encoded in batch
        )
        try:
            data = (
                await self._request(
                    "POST",
                    "/_bulk",
                    content=body,
                    headers={"Content-Type": "application/x-ndjson"},
                )
            ).json()
        except httpx.HTTPError as write_error:
            for source in documents:
                try:
                    current = (
                        (
                            await self._request(
                                "GET",
                                f"/{alias}/_doc/{self._document_id(source['document_id'])}",
                            )
                        )
                        .json()
                        .get("_source", {})
                    )
                except Exception as read_error:
                    raise RuntimeError(
                        "OpenSearch document write outcome could not be verified"
                    ) from read_error
                if not (
                    current.get("record_type") == "document"
                    and current.get("mutation_generation")
                    == source["mutation_generation"]
                    and current.get("mutation_digest") == source["mutation_digest"]
                ):
                    raise RuntimeError(
                        "OpenSearch document write outcome could not be verified"
                    ) from write_error
            # Every document in the batch was verified present at this exact
            # generation and digest, so the lost response hid a success.
            return set()
        items = data.get("items", [])
        return {
            source["document_id"]
            for position, source in enumerate(documents)
            if position >= len(items)
            or int(items[position].get("index", {}).get("status", 500)) >= 300
        }

    async def _bulk_to(self, alias: str, chunks: Sequence[DocumentChunk]) -> BulkResult:
        """Index whole documents, splitting into as many requests as needed."""
        if not chunks:
            return BulkResult(accepted=0)
        grouped = self._group_by_document(chunks)
        if len(grouped) > self.limits.max_bulk_documents:
            raise ValueError("bulk request exceeds document limit")
        encoded: list[tuple[dict, bytes]] = []
        failed_documents: set[str] = set()
        for document_id, document_chunks in grouped.items():
            try:
                encoded.append(self._encode_document(document_chunks))
            except ValueError:
                # One oversized document fails on its own; its neighbours in the
                # batch are unaffected and still land.
                failed_documents.add(document_id)
        for batch in self._batches(alias, encoded):
            failed_documents |= await self._send_batch(alias, batch)
        failed = tuple(
            chunk.chunk_id for chunk in chunks if chunk.document_id in failed_documents
        )
        return BulkResult(accepted=len(chunks) - len(failed), failed_ids=failed)

    async def _write_tombstone(self, alias: str, mutation: DocumentMutation) -> None:
        if mutation.generation < 1:
            raise ValueError("document mutation generation must be positive")
        source = {
            "record_type": "tombstone",
            "document_id": mutation.document_id,
            "mutation_generation": mutation.generation,
            "schema_version": INDEX_SCHEMA_VERSION,
        }
        path = f"/{alias}/_doc/{self._document_id(mutation.document_id)}"
        try:
            response = await self._client.put(
                path,
                params={
                    "version": mutation.generation * 2,
                    "version_type": "external",
                    "refresh": "true",
                },
                json=source,
            )
        except httpx.HTTPError as write_error:
            try:
                current = (await self._request("GET", path)).json().get("_source", {})
            except Exception as read_error:
                raise RuntimeError(
                    "OpenSearch tombstone outcome could not be verified"
                ) from read_error
            if current == source:
                return
            if int(current.get("mutation_generation", 0)) > mutation.generation:
                raise RuntimeError(
                    "document mutation generation is stale"
                ) from write_error
            raise RuntimeError(
                "OpenSearch tombstone outcome could not be verified"
            ) from write_error
        if response.status_code == 409:
            raise RuntimeError("document mutation generation is stale")
        response.raise_for_status()

    @staticmethod
    def _tombstone_source(mutation: DocumentMutation) -> dict:
        if mutation.generation < 1:
            raise ValueError("document mutation generation must be positive")
        return {
            "record_type": "tombstone",
            "document_id": mutation.document_id,
            "mutation_generation": mutation.generation,
            "schema_version": INDEX_SCHEMA_VERSION,
        }

    async def _write_tombstones(
        self, alias: str, mutations: Sequence[DocumentMutation]
    ) -> None:
        """Write every tombstone in one refreshed request.

        Refreshing here is an authorization property, not a performance choice:
        a revoked document must stop being searchable before its replacement is
        written. Doing it once per batch keeps that guarantee while removing the
        per-document refresh that made ingest unusable at corpus scale.
        """
        if not mutations:
            return
        if len(mutations) == 1:
            await self._write_tombstone(alias, mutations[0])
            return
        sources = [self._tombstone_source(mutation) for mutation in mutations]
        lines: list[bytes] = []
        for mutation, source in zip(mutations, sources):
            lines.append(
                json.dumps(
                    {
                        "index": {
                            "_index": alias,
                            "_id": self._document_id(mutation.document_id),
                            "version": mutation.generation * 2,
                            "version_type": "external",
                        }
                    },
                    separators=(",", ":"),
                ).encode()
            )
            lines.append(json.dumps(source, separators=(",", ":")).encode())
        body = b"\n".join(lines) + b"\n"
        try:
            data = (
                await self._request(
                    "POST",
                    "/_bulk",
                    params={"refresh": "true"},
                    content=body,
                    headers={"Content-Type": "application/x-ndjson"},
                )
            ).json()
        except httpx.HTTPError as write_error:
            # A lost response may still have committed. Re-read each one rather
            # than retrying a write that would now look stale to itself.
            for mutation, source in zip(mutations, sources):
                path = f"/{alias}/_doc/{self._document_id(mutation.document_id)}"
                try:
                    current = (
                        (await self._request("GET", path)).json().get("_source", {})
                    )
                except Exception as read_error:
                    raise RuntimeError(
                        "OpenSearch tombstone outcome could not be verified"
                    ) from read_error
                if current == source:
                    continue
                if int(current.get("mutation_generation", 0)) > mutation.generation:
                    raise RuntimeError(
                        "document mutation generation is stale"
                    ) from write_error
                raise RuntimeError(
                    "OpenSearch tombstone outcome could not be verified"
                ) from write_error
            return
        items = data.get("items", [])
        if len(items) != len(mutations):
            raise RuntimeError("OpenSearch tombstone outcome could not be verified")
        for item in items:
            status = int(item.get("index", {}).get("status", 500))
            if status == 409:
                raise RuntimeError("document mutation generation is stale")
            if status >= 300:
                raise RuntimeError("OpenSearch tombstone outcome could not be verified")

    async def bulk_index(self, chunks: Sequence[DocumentChunk]) -> BulkResult:
        async with self._mutation_lock:
            await self.ensure_index()
            grouped = self._group_by_document(chunks)
            if len(grouped) > self.limits.max_bulk_documents:
                raise ValueError("bulk request exceeds document limit")
            writable: list[DocumentChunk] = []
            mutations: list[DocumentMutation] = []
            failed: list[str] = []
            for document_id, document_chunks in grouped.items():
                try:
                    # Size the envelope before tombstoning it. Deleting a
                    # document we then cannot replace would destroy content.
                    source, _ = self._encode_document(document_chunks)
                except ValueError:
                    failed.extend(chunk.chunk_id for chunk in document_chunks)
                    continue
                writable.extend(document_chunks)
                mutations.append(
                    DocumentMutation(document_id, int(source["mutation_generation"]))
                )
            if not writable:
                return BulkResult(accepted=0, failed_ids=tuple(failed))
            # Delete first: an ACL revocation may temporarily reduce availability,
            # but old authorized content must never survive a failed replacement.
            # The even-version tombstone and odd-version atomic document envelope
            # make delayed lower-generation writes harmless across processes.
            await self._write_tombstones(self.write_alias, mutations)
            # One bulk action owns the whole document, so a failure leaves the
            # already-acknowledged tombstone rather than a partial document.
            result = await self._bulk_to(self.write_alias, writable)
            failed.extend(result.failed_ids)
            return BulkResult(
                accepted=len(chunks) - len(failed), failed_ids=tuple(failed)
            )

    async def delete_documents(self, mutations: Sequence[DocumentMutation]) -> int:
        async with self._mutation_lock:
            await self.ensure_index()
            if len(mutations) > self.limits.max_bulk_documents:
                raise ValueError("delete request exceeds document limit")
            await self._write_tombstones(self.write_alias, list(mutations))
            return len(mutations)

    def _search_body(self, request: SearchRequest) -> dict:
        query = request.query.strip()
        if not query or len(query) > self.limits.max_query_chars:
            raise ValueError("query is required and exceeds the configured bound")
        if not request.acl_tokens:
            raise ValueError("at least one ACL token is required")
        if len(request.acl_tokens) > 512 or any(
            not token or len(token) > 256 for token in request.acl_tokens
        ):
            raise ValueError("ACL token set exceeds the configured bound")
        limit = max(1, min(int(request.limit), self.limits.max_results))
        offset = max(0, int(request.offset))
        if offset > self.limits.max_offset:
            raise ValueError("search offset exceeds the configured bound")
        filters: list[dict] = [
            {"term": {"record_type": "document"}},
            {"terms": {"acl_tokens": list(request.acl_tokens)}},
        ]
        if request.filters.path_scopes:
            if len(request.filters.path_scopes) > 100:
                raise ValueError("search path scopes exceed the configured bound")
            filters.append(
                {
                    "bool": {
                        "minimum_should_match": 1,
                        "should": [
                            {
                                "bool": {
                                    "filter": [
                                        {"term": {"share_id": share_id}},
                                        {
                                            "prefix": {
                                                "relative_path": {
                                                    "value": root.rstrip("\\") + "\\",
                                                    "case_insensitive": True,
                                                }
                                            }
                                        },
                                    ]
                                }
                            }
                            for share_id, root in request.filters.path_scopes
                        ],
                    }
                }
            )
        # Windows resolves an explicit DENY ahead of any allow, including one a
        # user inherits through a group. Expressing that as must_not over the
        # same principal set keeps a denied user from reading a document some
        # other group grants them.
        deny_filter = {"terms": {"deny_acl_tokens": list(request.acl_tokens)}}
        terms = {
            "share_id": request.filters.share_ids,
            "matter_ids": request.filters.matter_ids,
            "extension": tuple(e.lower() for e in request.filters.extensions),
            "document_id": request.filters.document_ids,
        }
        if any(len(values) > 100 for values in terms.values()):
            raise ValueError("field filter exceeds the configured bound")
        filters.extend(
            {"terms": {name: list(values)}} for name, values in terms.items() if values
        )
        date_range = {}
        if request.filters.modified_after:
            date_range["gte"] = request.filters.modified_after.isoformat()
        if request.filters.modified_before:
            date_range["lte"] = request.filters.modified_before.isoformat()
        if date_range:
            filters.append({"range": {"modified_at": date_range}})
        body = {
            "from": offset,
            "size": limit,
            "track_total_hits": True,
            "timeout": f"{max(50, min(request.timeout_ms, 10_000))}ms",
            "_source": [
                "document_version",
                "document_id",
                "share_id",
                "relative_path",
                "filename",
                "extension",
            ],
            "query": {
                "bool": {
                    "must": [
                        {
                            "nested": {
                                "path": "chunks",
                                "score_mode": "max",
                                "query": {
                                    "query_string": {
                                        "query": query,
                                        "fields": [
                                            "chunks.content^3",
                                            "chunks.filename^2",
                                            "chunks.section_text^2",
                                        ],
                                        "default_operator": "AND",
                                        "allow_leading_wildcard": False,
                                        "analyze_wildcard": False,
                                        "lenient": False,
                                        "max_determinized_states": 1_000,
                                        "fuzzy_max_expansions": 20,
                                        "fuzzy_prefix_length": 2,
                                    }
                                },
                                "inner_hits": {
                                    "size": 1,
                                    "_source": [
                                        "chunks.chunk_id",
                                        "chunks.page_number",
                                        "chunks.section_path",
                                        "chunks.ordinal",
                                    ],
                                },
                            }
                        }
                    ],
                    "filter": filters,
                    "must_not": [deny_filter],
                }
            },
        }
        if request.highlight:
            body["query"]["bool"]["must"][0]["nested"]["inner_hits"]["highlight"] = {
                "encoder": "html",
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "fields": {
                    "chunks.content": {
                        "fragment_size": 240,
                        "number_of_fragments": 1,
                    }
                },
                "max_analyzer_offset": 1_000_000,
            }
        return body

    async def search(self, request: SearchRequest) -> SearchResponse:
        active = await self.ensure_index()
        if (
            await self._rebuild_lease_state() is not None
            or await self._index_write_blocked(active)
        ):
            raise SearchUnavailableError(
                "OpenSearch search is unavailable during rebuild quarantine"
            )
        data = (
            await self._request(
                "POST", f"/{self.read_alias}/_search", json=self._search_body(request)
            )
        ).json()
        shards = data.get("_shards", {})
        if int(shards.get("failed", 0)):
            raise RuntimeError("OpenSearch search was incomplete")
        hits = []
        for item in data.get("hits", {}).get("hits", []):
            source = item.get("_source", {})
            inner = (
                item.get("inner_hits", {})
                .get("chunks", {})
                .get("hits", {})
                .get("hits", [])
            )
            if not inner:
                raise RuntimeError("OpenSearch nested chunk result was incomplete")
            chunk = inner[0].get("_source", {})
            fragments = inner[0].get("highlight", {}).get("chunks.content", [])
            hits.append(
                SearchHit(
                    document_id=str(source.get("document_id", "")),
                    document_version=str(source.get("document_version", "")),
                    chunk_id=str(chunk.get("chunk_id", "")),
                    share_id=str(source.get("share_id", "")),
                    relative_path=str(source.get("relative_path", "")),
                    filename=str(source.get("filename", "")),
                    extension=str(source.get("extension", "")),
                    score=float(item.get("_score") or 0),
                    snippet=str(fragments[0] if fragments else "")[:1_000],
                    page_number=chunk.get("page_number"),
                    section_path=tuple(chunk.get("section_path") or ()),
                    ordinal=int(chunk.get("ordinal") or 0),
                )
            )
        total = data.get("hits", {}).get("total", 0)
        if isinstance(total, dict):
            if total.get("relation", "eq") != "eq":
                raise RuntimeError("OpenSearch total hit count was incomplete")
            total = total.get("value", 0)
        return SearchResponse(
            hits=tuple(hits),
            total=int(total),
            took_ms=int(data.get("took", 0)),
            timed_out=bool(data.get("timed_out", False)),
            engine="opensearch",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )

    async def rebuild(self, chunks: AsyncIterator[DocumentChunk]) -> str:
        async with self._mutation_lock:
            return await self._rebuild_unlocked(chunks)

    async def _replay_index(
        self,
        source_index: str,
        destination_index: str,
        lease: tuple[str, int, int] | None = None,
    ) -> dict:
        body = {
            "conflicts": "proceed",
            "source": {"index": source_index},
            "dest": {"index": destination_index, "version_type": "external"},
        }
        try:
            start = (
                await self._request(
                    "POST",
                    "/_reindex",
                    params={"wait_for_completion": "false", "refresh": "true"},
                    json=body,
                )
            ).json()
            if not isinstance(start, dict):
                raise ValueError("OpenSearch rebuild replay start was not an object")
        except (httpx.HTTPError, ValueError) as exc:
            # Never retry an ambiguous start: the first task may be live and a
            # second task would no longer be tracked by the durable lease.
            raise RebuildReplayUncertain(
                "OpenSearch rebuild replay start outcome is uncertain"
            ) from exc
        task_id = str(start.get("task", ""))
        if not task_id or "/" in task_id or len(task_id) > 256:
            raise RebuildReplayUncertain(
                "OpenSearch rebuild replay task was not identified"
            )
        if lease is not None:
            await self._update_rebuild_lease(
                lease, phase="replaying", task_id=task_id, source_write_blocked=True
            )
        deadline = time.monotonic() + self.limits.rebuild_replay_timeout_seconds
        while True:
            try:
                task = (await self._request("GET", f"/_tasks/{task_id}")).json()
                if not isinstance(task, dict):
                    raise ValueError("OpenSearch rebuild task state was not an object")
            except (httpx.HTTPError, ValueError) as exc:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild replay task state is uncertain"
                ) from exc
            if task.get("completed") is True:
                if task.get("error") or not isinstance(task.get("response"), dict):
                    raise RuntimeError("OpenSearch rebuild replay task failed")
                if lease is not None:
                    await self._update_rebuild_lease(
                        lease,
                        phase="replay_complete",
                        task_id=task_id,
                        source_write_blocked=True,
                    )
                return task["response"]
            if task.get("completed") is not False:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild replay task state was malformed"
                )
            if time.monotonic() >= deadline:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild replay did not complete before quarantine"
                )
            await asyncio.sleep(self.limits.rebuild_replay_poll_seconds)

    async def _acquire_rebuild_lease(
        self,
        source_index: str | None,
        candidate_index: str,
        *,
        phase: str = "building",
    ) -> tuple[str, int, int]:
        properties = {
            "owner": {"type": "keyword"},
            "created_at": {"type": "date"},
            "source_index": {"type": "keyword"},
            "candidate_index": {"type": "keyword"},
            "phase": {"type": "keyword"},
            "source_write_blocked": {"type": "boolean"},
            "task_id": {"type": "keyword"},
            "last_verified_alias": {"type": "keyword"},
        }
        mapping = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "dynamic": "strict",
                "properties": properties,
            },
        }
        response = await self._client.put(f"/{self.coordination_index}", json=mapping)
        if response.status_code not in {200, 201, 400}:
            response.raise_for_status()
        if response.status_code == 400 and (
            response.json().get("error", {}).get("type")
            != "resource_already_exists_exception"
        ):
            response.raise_for_status()
        if response.status_code == 400:
            await self._request(
                "PUT",
                f"/{self.coordination_index}/_mapping",
                json={"properties": properties},
            )

        owner = uuid.uuid4().hex
        create_path = f"/{self.coordination_index}/_create/rebuild-lock"
        document_path = f"/{self.coordination_index}/_doc/rebuild-lock"
        source = {
            "owner": owner,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_index": source_index or "",
            "candidate_index": candidate_index,
            "phase": phase,
            "source_write_blocked": False,
            "task_id": "",
            "last_verified_alias": source_index or "",
        }
        try:
            lease = await self._client.put(
                create_path, params={"refresh": "true"}, json=source
            )
        except httpx.TransportError as write_error:
            try:
                current = (await self._request("GET", document_path)).json()
            except Exception as read_error:
                raise RuntimeError(
                    "OpenSearch rebuild lease outcome could not be verified"
                ) from read_error
            if current.get("_source", {}).get("owner") != owner:
                raise RuntimeError(
                    "another OpenSearch rebuild owns the lease"
                ) from write_error
            return owner, int(current["_seq_no"]), int(current["_primary_term"])
        if lease.status_code == 409:
            raise RuntimeError("another OpenSearch rebuild owns the lease")
        lease.raise_for_status()
        data = lease.json()
        return owner, int(data["_seq_no"]), int(data["_primary_term"])

    async def _rebuild_lease_state(self) -> dict | None:
        response = await self._client.get(
            f"/{self.coordination_index}/_doc/rebuild-lock"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        source = data.get("_source")
        if not isinstance(source, dict):
            raise RuntimeError("OpenSearch rebuild lease state was malformed")
        return data

    async def _update_rebuild_lease(
        self, lease: tuple[str, int, int], **updates: object
    ) -> None:
        owner, _seq_no, _primary_term = lease
        path = f"/{self.coordination_index}/_doc/rebuild-lock"
        try:
            current = await self._rebuild_lease_state()
        except Exception as read_error:
            raise RebuildReplayUncertain(
                "OpenSearch rebuild lease state could not be verified"
            ) from read_error
        if current is None or current.get("_source", {}).get("owner") != owner:
            raise RebuildReplayUncertain(
                "OpenSearch rebuild lease ownership is uncertain"
            )
        source = dict(current["_source"])
        source.update(updates)
        params = {
            "if_seq_no": int(current["_seq_no"]),
            "if_primary_term": int(current["_primary_term"]),
            "refresh": "true",
        }
        try:
            response = await self._client.put(path, params=params, json=source)
        except httpx.TransportError as write_error:
            try:
                resolved = await self._rebuild_lease_state()
            except Exception as read_error:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild lease update could not be verified"
                ) from read_error
            if resolved is None or resolved.get("_source") != source:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild lease update could not be verified"
                ) from write_error
            return
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as write_error:
            message = (
                "OpenSearch rebuild lease changed concurrently"
                if response.status_code == 409
                else "OpenSearch rebuild lease update could not be verified"
            )
            raise RebuildReplayUncertain(message) from write_error

    async def _release_rebuild_lease(self, lease: tuple[str, int, int]) -> None:
        owner, _seq_no, _primary_term = lease
        path = f"/{self.coordination_index}/_doc/rebuild-lock"
        current_response = await self._client.get(path)
        if current_response.status_code == 404:
            return
        current_response.raise_for_status()
        current = current_response.json()
        if current.get("_source", {}).get("owner") != owner:
            raise RuntimeError("OpenSearch rebuild lease ownership changed")
        params = {
            "if_seq_no": int(current["_seq_no"]),
            "if_primary_term": int(current["_primary_term"]),
            "refresh": "true",
        }
        try:
            response = await self._client.delete(path, params=params)
        except httpx.TransportError as delete_error:
            resolved = await self._client.get(path)
            if resolved.status_code == 404:
                return
            raise RuntimeError(
                "OpenSearch rebuild lease release could not be verified"
            ) from delete_error
        response.raise_for_status()

    async def _unblock_index_writes(self, index: str) -> None:
        await self._request(
            "PUT", f"/{index}/_settings", json={"index.blocks.write": None}
        )
        if await self._index_write_blocked(index):
            raise RebuildReplayUncertain(
                "OpenSearch rebuild write block could not be removed"
            )

    async def _refresh_index(self, index: str) -> None:
        refresh = (await self._request("POST", f"/{index}/_refresh")).json()
        shards = refresh.get("_shards", {})
        total = int(shards.get("total", 0))
        if (
            total < 1
            or int(shards.get("successful", -1)) != total
            or int(shards.get("failed", -1)) != 0
        ):
            raise RuntimeError("OpenSearch index refresh was incomplete")

    async def _index_write_blocked(self, index: str) -> bool:
        settings = (
            await self._request(
                "GET",
                f"/{index}/_settings",
                params={"flat_settings": "true", "include_defaults": "true"},
            )
        ).json()
        value = settings.get(index, {}).get("settings", {}).get("index.blocks.write")
        return value in {True, "true"}

    async def _rebuild_unlocked(self, chunks: AsyncIterator[DocumentChunk]) -> str:
        # Establish a canonical generation under the distributed lease before
        # capturing the replay source. This closes the first-generation race
        # with another process initializing aliases and accepting mutations.
        prior_index = await self.ensure_index()
        index = self._new_index_name()
        lease = await self._acquire_rebuild_lease(prior_index, index)
        batch: list[DocumentChunk] = []
        document_batch: list[DocumentChunk] = []
        current_document: str | None = None
        current_version: str | None = None
        current_generation: int | None = None
        prior_blocked = False

        batched_documents = 0

        async def flush_document() -> None:
            nonlocal batch, document_batch, batched_documents
            if not document_batch:
                return
            if len(document_batch) > self.limits.max_document_chunks:
                raise ValueError("one rebuild document exceeds the chunk limit")
            # Bound the accumulator by whole documents. _bulk_to splits the
            # batch again by byte budget, so a document is never rejected for
            # the size of the batch it happened to arrive in.
            if batch and batched_documents >= self.limits.max_bulk_documents:
                result = await self._bulk_to(index, batch)
                if result.failed_ids:
                    raise RuntimeError("OpenSearch rebuild bulk indexing failed")
                batch = []
                batched_documents = 0
            batch.extend(document_batch)
            batched_documents += 1
            document_batch = []

        try:
            await self._request("PUT", f"/{index}", json=self._mapping())
            async for chunk in chunks:
                version = chunk.document_version or chunk.content_hash
                if (
                    current_document is not None
                    and chunk.document_id < current_document
                ):
                    raise ValueError("rebuild chunks must be ordered by document_id")
                if chunk.document_id != current_document:
                    await flush_document()
                    current_document = chunk.document_id
                    current_version = version
                    current_generation = chunk.mutation_generation
                elif (
                    current_version != version
                    or current_generation != chunk.mutation_generation
                ):
                    raise ValueError(
                        "rebuild contains multiple versions or generations of one document"
                    )
                document_batch.append(chunk)
                if len(document_batch) > self.limits.max_document_chunks:
                    raise ValueError("one rebuild document exceeds the chunk limit")
            await flush_document()
            if batch:
                result = await self._bulk_to(index, batch)
                if result.failed_ids:
                    raise RuntimeError("OpenSearch rebuild bulk indexing failed")
            await self._refresh_index(index)
            # The add-block API drains in-flight writes before returning. Once
            # blocked, replay the stable old index with source external versions
            # so mutations newer than the rebuild input win.
            if await self._index_write_blocked(prior_index):
                raise RuntimeError("OpenSearch active index is already write-blocked")
            await self._update_rebuild_lease(
                lease,
                phase="blocking",
                source_write_blocked=False,
                last_verified_alias=prior_index,
            )
            # Mark intent before the request: if its acknowledgement is lost,
            # the block may still commit later, so retain quarantine rather
            # than attempting a racy compensating unblock.
            prior_blocked = True
            try:
                block_result = (
                    await self._request("PUT", f"/{prior_index}/_block/write")
                ).json()
                if not isinstance(block_result, dict):
                    raise ValueError(
                        "OpenSearch write-block response was not an object"
                    )
            except Exception as block_error:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild write block outcome is uncertain"
                ) from block_error
            if (
                block_result.get("acknowledged") is not True
                or block_result.get("shards_acknowledged") is not True
            ):
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild write block was not fully acknowledged"
                )
            if await self._active_index() != prior_index:
                raise RuntimeError(
                    "OpenSearch active index changed during rebuild cutover"
                )
            await self._refresh_index(prior_index)
            await self._update_rebuild_lease(
                lease,
                phase="blocked",
                source_write_blocked=True,
                last_verified_alias=prior_index,
            )
            replay = await self._replay_index(prior_index, index, lease)
            total = int(replay.get("total", -1))
            accounted = sum(
                int(replay.get(key, 0))
                for key in ("created", "updated", "version_conflicts", "noops")
            )
            if (
                bool(replay.get("timed_out", False))
                or replay.get("failures")
                or total < 0
                or accounted != total
            ):
                raise RuntimeError("OpenSearch rebuild mutation replay was incomplete")
            await self._refresh_index(index)
            await self._update_rebuild_lease(
                lease,
                phase="cutover",
                source_write_blocked=True,
                last_verified_alias=prior_index,
            )
            await self._commit_aliases(index)
            await self._update_rebuild_lease(
                lease,
                phase="complete",
                source_write_blocked=True,
                last_verified_alias=index,
            )
            await self._release_rebuild_lease(lease)
            return index
        except RebuildReplayUncertain:
            # A background task or block transition may still be active. Keep
            # the source blocked, candidate intact, owner lease present, and
            # last successfully recorded phase unchanged for recovery proof.
            raise
        except Exception:
            if prior_blocked:
                try:
                    await self._unblock_index_writes(prior_index)
                except Exception as unblock_error:
                    raise RebuildReplayUncertain(
                        "OpenSearch rebuild failure is quarantined"
                    ) from unblock_error
            verified_inactive = False
            try:
                verified_inactive = await self._active_index() != index
            except Exception:
                # Unknown alias state: retain the generation for operator review.
                verified_inactive = False
            if verified_inactive:
                try:
                    await self._client.delete(f"/{index}")
                except httpx.HTTPError:
                    pass
            try:
                await self._release_rebuild_lease(lease)
            except Exception as release_error:
                raise RebuildReplayUncertain(
                    "OpenSearch rebuild lease release is uncertain"
                ) from release_error
            raise

    async def health(self) -> EngineHealth:
        try:
            cluster = (await self._request("GET", "/_cluster/health")).json()
            active = await self._active_index()
            settings = (
                await self._request(
                    "GET",
                    "/_cluster/settings",
                    params={"include_defaults": "true", "flat_settings": "true"},
                )
            ).json()
            watermarks = {}
            for label, suffix in (
                ("low", "low"),
                ("high", "high"),
                ("flood_stage", "flood_stage"),
            ):
                key = f"cluster.routing.allocation.disk.watermark.{suffix}"
                watermarks[label] = next(
                    (
                        settings.get(scope, {}).get(key)
                        for scope in ("transient", "persistent", "defaults")
                        if settings.get(scope, {}).get(key) is not None
                    ),
                    None,
                )
            threshold_key = "cluster.routing.allocation.disk.threshold_enabled"
            threshold_enabled = next(
                (
                    settings.get(scope, {}).get(threshold_key)
                    for scope in ("transient", "persistent", "defaults")
                    if settings.get(scope, {}).get(threshold_key) is not None
                ),
                None,
            )
            expected_watermarks = {
                "low": self.limits.disk_watermark_low,
                "high": self.limits.disk_watermark_high,
                "flood_stage": self.limits.disk_watermark_flood,
            }
            watermarks_match = watermarks == expected_watermarks
            status = str(cluster.get("status", "unknown"))
            cluster_timed_out = bool(cluster.get("timed_out", False))
            active_write_blocked = bool(
                active and await self._index_write_blocked(active)
            )
            rebuild_lease_active = await self._rebuild_lease_state() is not None
            return EngineHealth(
                status="healthy"
                if status in {"green", "yellow"}
                and active
                and watermarks_match
                and threshold_enabled in {True, "true"}
                and not cluster_timed_out
                and not active_write_blocked
                and not rebuild_lease_active
                else "degraded",
                engine="opensearch",
                index_schema_version=INDEX_SCHEMA_VERSION,
                active_index=active,
                details={
                    "cluster_status": status,
                    "timed_out": cluster_timed_out,
                    "capabilities": self.capabilities,
                    "disk_watermarks": watermarks,
                    "expected_disk_watermarks": expected_watermarks,
                    "disk_threshold_enabled": threshold_enabled,
                    "active_index_write_blocked": active_write_blocked,
                    "rebuild_lease_active": rebuild_lease_active,
                },
            )
        except (httpx.HTTPError, ValueError) as exc:
            return EngineHealth(
                status="unavailable",
                engine="opensearch",
                index_schema_version=INDEX_SCHEMA_VERSION,
                active_index=None,
                details={
                    "error": type(exc).__name__,
                    "capabilities": self.capabilities,
                },
            )

    async def create_snapshot(self, repository: str, snapshot: str) -> dict:
        self._validate_snapshot_name(repository)
        self._validate_snapshot_name(snapshot)
        return (
            await self._request(
                "PUT",
                f"/_snapshot/{repository}/{snapshot}",
                params={"wait_for_completion": "false"},
                json={
                    "indices": f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-*",
                    "include_global_state": False,
                },
            )
        ).json()

    async def restore_snapshot(self, repository: str, snapshot: str) -> dict:
        self._validate_snapshot_name(repository)
        self._validate_snapshot_name(snapshot)
        async with self._mutation_lock:
            metadata = (
                await self._request("GET", f"/_snapshot/{repository}/{snapshot}")
            ).json()
            snapshots = metadata.get("snapshots", [])
            if len(snapshots) != 1:
                raise RuntimeError("OpenSearch snapshot metadata was incomplete")
            snapshot_result = snapshots[0]
            shard_result = snapshot_result.get("shards", {})
            indices = snapshot_result.get("indices", [])
            prefix = f"{self.index_prefix}-v{INDEX_SCHEMA_VERSION}-"
            if (
                snapshot_result.get("state") != "SUCCESS"
                or not indices
                or any(not str(index).startswith(prefix) for index in indices)
                or int(shard_result.get("total", 0)) < 1
                or int(shard_result.get("successful", -1))
                != int(shard_result.get("total", 0))
                or int(shard_result.get("failed", -1)) != 0
            ):
                raise RuntimeError("OpenSearch snapshot is not exactly restorable")
            restore_body = {
                "indices": ",".join(indices),
                "include_aliases": False,
                "include_global_state": False,
            }
            try:
                accepted = (
                    await self._request(
                        "POST",
                        f"/_snapshot/{repository}/{snapshot}/_restore",
                        params={"wait_for_completion": "false"},
                        json=restore_body,
                    )
                ).json()
                if accepted.get("accepted") is not True:
                    raise RuntimeError("OpenSearch snapshot restore was not accepted")
            except httpx.TransportError:
                # A lost acknowledgement can follow a committed restore. Recovery
                # status below is the authoritative completion signal.
                pass

            deadline = time.monotonic() + self.limits.restore_timeout_seconds
            recovery_path = f"/{','.join(indices)}/_recovery"
            expected_shards = int(shard_result["total"])
            while True:
                recovery = (
                    await self._request(
                        "GET", recovery_path, params={"active_only": "false"}
                    )
                ).json()
                primary_shards: set[tuple[str, int]] = set()
                complete = True
                for index in indices:
                    shards = recovery.get(index, {}).get("shards", [])
                    primaries = [
                        shard for shard in shards if shard.get("primary") is True
                    ]
                    if not primaries:
                        complete = False
                        break
                    for shard in primaries:
                        source = shard.get("source", {})
                        shard_id = shard.get("id")
                        if (
                            shard.get("stage") != "DONE"
                            or shard.get("type") != "SNAPSHOT"
                            or source.get("repository") != repository
                            or source.get("snapshot") != snapshot
                            or not isinstance(shard_id, int)
                        ):
                            complete = False
                            break
                        primary_shards.add((index, shard_id))
                    if not complete:
                        break
                if complete and len(primary_shards) == expected_shards:
                    return {
                        "accepted": True,
                        "state": "SUCCESS",
                        "indices": list(indices),
                    }
                if time.monotonic() >= deadline:
                    raise TimeoutError("OpenSearch snapshot restore did not complete")
                await asyncio.sleep(self.limits.restore_poll_seconds)

    @staticmethod
    def _validate_snapshot_name(value: str) -> None:
        if not value or not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("snapshot and repository names must be simple identifiers")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

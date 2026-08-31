from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Protocol

import httpx

from .database import connect
from .loader import chunk_text, init_schema

COURTLISTENER_API_BASE = "https://www.courtlistener.com/api/rest/v4"
COURTLISTENER_WEB_BASE = "https://www.courtlistener.com"
OHIO_SOURCE_KEY = "courtlistener:ohio-caselaw"
DEFAULT_BASELINE_START = date(2015, 1, 1)
DEFAULT_OVERLAP_HOURS = 48


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return " ".join(html.unescape(value).split())
    text = " ".join(" ".join(parser.parts).split())
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def resource_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return resource_id(value.get("id") or value.get("resource_uri"))
    match = re.search(r"/(\d+)/?$", str(value))
    if match:
        return int(match.group(1))
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def canonical_web_url(*values: Any) -> str | None:
    for value in values:
        if not value:
            continue
        text = str(value)
        if text.startswith("https://") or text.startswith("http://"):
            return text
        if text.startswith("/"):
            return f"{COURTLISTENER_WEB_BASE}{text}"
    return None


def is_ohio_appellate_court(court: dict[str, Any]) -> bool:
    court_id = str(court.get("id") or court.get("court_id") or "").lower()
    name = " ".join(
        str(court.get(key) or "")
        for key in ("full_name", "short_name", "citation_string", "jurisdiction")
    ).lower()
    if court_id in {"ohio", "ohioctapp"}:
        return True
    if "supreme court of ohio" in name or "ohio supreme court" in name:
        return True
    return "ohio" in name and (
        "court of appeals" in name or "appellate court" in name
    )


@dataclass(frozen=True)
class SyncConfig:
    api_key: str
    api_base: str = COURTLISTENER_API_BASE
    baseline_start: date = DEFAULT_BASELINE_START
    overlap_hours: int = DEFAULT_OVERLAP_HOURS
    timeout_seconds: float = 30.0
    max_pages: int | None = None
    court_ids: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "SyncConfig":
        raw_courts = os.getenv("COURTLISTENER_SYNC_COURT_IDS", "")
        raw_start = os.getenv(
            "COURTLISTENER_SYNC_BASELINE_START", DEFAULT_BASELINE_START.isoformat()
        )
        raw_max_pages = os.getenv("COURTLISTENER_SYNC_MAX_PAGES")
        return cls(
            api_key=os.getenv("COURTLISTENER_API_KEY", "").strip(),
            api_base=os.getenv("COURTLISTENER_API_BASE", COURTLISTENER_API_BASE).rstrip(
                "/"
            ),
            baseline_start=date.fromisoformat(raw_start),
            overlap_hours=int(
                os.getenv("COURTLISTENER_SYNC_OVERLAP_HOURS", DEFAULT_OVERLAP_HOURS)
            ),
            timeout_seconds=float(
                os.getenv("COURTLISTENER_SYNC_TIMEOUT_SECONDS", "30")
            ),
            max_pages=int(raw_max_pages) if raw_max_pages else None,
            court_ids=tuple(
                part.strip() for part in raw_courts.split(",") if part.strip()
            ),
        )


class CourtListenerClient:
    def __init__(self, config: SyncConfig, transport: httpx.BaseTransport | None = None):
        if not config.api_key:
            raise RuntimeError("COURTLISTENER_API_KEY is required for REST synchronization")
        self.config = config
        self.http = httpx.Client(
            headers={
                "Authorization": f"Token {config.api_key}",
                "Accept": "application/json",
                "User-Agent": "LegalApp-CourtListener-Sync/1.0",
            },
            timeout=config.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self._resource_cache: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.http.close()

    def get_json(self, url: str) -> dict[str, Any]:
        response = self.http.get(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"CourtListener returned a non-object response for {url}")
        return payload

    def get_resource(self, value: Any, endpoint: str) -> dict[str, Any]:
        object_id = resource_id(value)
        if object_id is None:
            raise RuntimeError(f"CourtListener {endpoint} resource has no usable ID")
        url = f"{self.config.api_base}/{endpoint}/{object_id}/"
        if url not in self._resource_cache:
            self._resource_cache[url] = self.get_json(url)
        return self._resource_cache[url]

    def list_url(self, endpoint: str, params: dict[str, Any]) -> str:
        return str(httpx.URL(f"{self.config.api_base}/{endpoint}/", params=params))

    def discover_ohio_courts(self) -> list[dict[str, Any]]:
        if self.config.court_ids:
            return [
                self.get_json(f"{self.config.api_base}/courts/{court_id}/")
                for court_id in self.config.court_ids
            ]
        url: str | None = self.list_url("courts", {"order_by": "id"})
        matches: list[dict[str, Any]] = []
        while url:
            page = self.get_json(url)
            for court in page.get("results") or []:
                if isinstance(court, dict) and is_ohio_appellate_court(court):
                    matches.append(court)
            url = page.get("next")
        if not matches:
            raise RuntimeError(
                "No Ohio Supreme/appellate courts were discovered; set "
                "COURTLISTENER_SYNC_COURT_IDS after reviewing the Courts API"
            )
        return matches

    def opinions_url(
        self,
        court_id: str,
        *,
        baseline_start: date,
        checkpoint_at: datetime | None,
        overlap_hours: int,
    ) -> str:
        params: dict[str, Any] = {
            "cluster__docket__court": court_id,
            "order_by": "date_modified,id",
        }
        if checkpoint_at:
            since = checkpoint_at - timedelta(hours=overlap_hours)
            params["date_modified__gte"] = since.isoformat()
        else:
            params["cluster__date_filed__gte"] = baseline_start.isoformat()
        return self.list_url("opinions", params)

    def opinion_bundle(
        self, court: dict[str, Any], opinion: dict[str, Any]
    ) -> dict[str, Any]:
        cluster_ref = opinion.get("cluster") or opinion.get("cluster_id")
        cluster = self.get_resource(cluster_ref, "clusters")
        docket_ref = cluster.get("docket") or cluster.get("docket_id")
        docket = self.get_resource(docket_ref, "dockets")
        return {"court": court, "docket": docket, "cluster": cluster, "opinion": opinion}


class SyncStore(Protocol):
    def ensure_source(self, baseline_start: date) -> None: ...

    def start_partition(self, court_id: str) -> dict[str, Any] | None: ...

    def begin_run(self, court_id: str, baseline_start: date) -> Any: ...

    def ingest_bundle(self, bundle: dict[str, Any]) -> tuple[int, int]: ...

    def record_page(
        self, court_id: str, next_url: str | None, rows: int, chunks: int
    ) -> None: ...

    def pause_run(self, run_id: Any, rows: int, chunks: int) -> None: ...

    def finish_partition(
        self,
        court_id: str,
        run_id: Any,
        checkpoint_at: datetime,
        rows: int,
        chunks: int,
    ) -> None: ...

    def fail_partition(self, court_id: str, run_id: Any, error: str) -> None: ...

    def unlock_partition(self, court_id: str) -> None: ...


class PostgresSyncStore:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _source_lock_key(court_id: str) -> str:
        return f"{OHIO_SOURCE_KEY}:{court_id}"

    def ensure_source(self, baseline_start: date) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO legal_sources (
                    source_key, display_name, description, publisher, source_type,
                    jurisdiction, canonical_url, authority_tier, official_status,
                    ingestion_mode, storage_policy, access_type, license_status,
                    sync_frequency, data_format, corpus_table, enabled, priority,
                    coverage_start, coverage_kind, parser_version, embedding_model,
                    embedding_version, licensing_notes, metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'binding_primary', 'aggregator',
                    'api', 'metadata_only', 'api_key', 'review_required',
                    'daily', 'json', 'opinions/opinion_chunks', false, 10,
                    %s, 'bounded', %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (source_key) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    authority_tier = EXCLUDED.authority_tier,
                    official_status = EXCLUDED.official_status,
                    ingestion_mode = EXCLUDED.ingestion_mode,
                    access_type = EXCLUDED.access_type,
                    sync_frequency = EXCLUDED.sync_frequency,
                    data_format = EXCLUDED.data_format,
                    corpus_table = EXCLUDED.corpus_table,
                    priority = EXCLUDED.priority,
                    coverage_start = EXCLUDED.coverage_start,
                    parser_version = EXCLUDED.parser_version,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding_version = EXCLUDED.embedding_version,
                    licensing_notes = EXCLUDED.licensing_notes,
                    metadata = legal_sources.metadata || EXCLUDED.metadata,
                    updated_at = now()
                """,
                [
                    OHIO_SOURCE_KEY,
                    "Ohio appellate case law (CourtListener)",
                    "Ohio Supreme Court and appellate opinions with citations and docket metadata.",
                    "Free Law Project / CourtListener",
                    "case_law",
                    "OH",
                    "https://www.courtlistener.com/",
                    baseline_start,
                    "courtlistener-rest-v4-html-with-citations-v1",
                    "mixedbread-ai/mxbai-embed-large-v1",
                    1,
                    "API use and stored volume require the configured CourtListener membership/commercial terms.",
                    json.dumps({"coverage_label": f"Ohio appellate baseline from {baseline_start}"}),
                ],
            )
        self.conn.commit()

    def start_partition(self, court_id: str) -> dict[str, Any] | None:
        lock_key = self._source_lock_key(court_id)
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [lock_key])
            row = cur.fetchone()
            if not row or not row[0]:
                self.conn.rollback()
                return None
            cur.execute(
                """
                INSERT INTO source_sync_states (source_key, partition_key, status)
                VALUES (%s, %s, 'idle')
                ON CONFLICT (source_key, partition_key) DO NOTHING
                """,
                [OHIO_SOURCE_KEY, court_id],
            )
            cur.execute(
                """
                SELECT checkpoint_at, cursor_url, status, rows_processed, chunks_created
                FROM source_sync_states
                WHERE source_key = %s AND partition_key = %s
                """,
                [OHIO_SOURCE_KEY, court_id],
            )
            state = cur.fetchone()
            cur.execute(
                """
                UPDATE source_sync_states
                SET status = 'running', last_attempted_at = now(), last_error = NULL,
                    updated_at = now()
                WHERE source_key = %s AND partition_key = %s
                """,
                [OHIO_SOURCE_KEY, court_id],
            )
            cur.execute(
                """
                UPDATE legal_sources
                SET last_attempted_at = now(), current_error = NULL, updated_at = now()
                WHERE source_key = %s
                """,
                [OHIO_SOURCE_KEY],
            )
        self.conn.commit()
        return {
            "checkpoint_at": state[0] if state else None,
            "cursor_url": state[1] if state else None,
            "status": state[2] if state else "idle",
            "rows_processed": state[3] if state else 0,
            "chunks_created": state[4] if state else 0,
        }

    def begin_run(self, court_id: str, baseline_start: date) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingest_runs (source, snapshot_date, status)
                VALUES (%s, %s, 'running')
                RETURNING id
                """,
                [f"{OHIO_SOURCE_KEY}:{court_id}", baseline_start],
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    @staticmethod
    def _court_id(court: dict[str, Any]) -> str:
        return str(court.get("id") or court.get("court_id") or "")

    def ingest_bundle(self, bundle: dict[str, Any]) -> tuple[int, int]:
        court = bundle["court"]
        docket = bundle["docket"]
        cluster = bundle["cluster"]
        opinion = bundle["opinion"]
        court_id = self._court_id(court)
        docket_id = resource_id(docket.get("id"))
        cluster_id = resource_id(cluster.get("id"))
        opinion_id = resource_id(opinion.get("id"))
        if not court_id or docket_id is None or cluster_id is None or opinion_id is None:
            raise RuntimeError("CourtListener bundle is missing a required stable identifier")

        html_value = (
            opinion.get("html_with_citations")
            or opinion.get("html")
            or opinion.get("xml_harvard")
            or ""
        )
        plain_value = opinion.get("plain_text") or ""
        searchable_text = plain_value.strip() or html_to_text(html_value)
        content_hash = hashlib.sha256(searchable_text.encode("utf-8")).hexdigest()
        source_url = canonical_web_url(
            opinion.get("absolute_url"),
            cluster.get("absolute_url"),
            opinion.get("download_url"),
        )
        citations = cluster.get("citations") or []
        if not isinstance(citations, (list, dict)):
            citations = []

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO courts (court_id, short_name, full_name, jurisdiction, metadata)
                VALUES (%s, %s, %s, 'OH', %s::jsonb)
                ON CONFLICT (court_id) DO UPDATE
                SET short_name = EXCLUDED.short_name,
                    full_name = EXCLUDED.full_name,
                    jurisdiction = 'OH',
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                [
                    court_id,
                    court.get("short_name"),
                    court.get("full_name") or court.get("short_name") or court_id,
                    json.dumps(court),
                ],
            )
            cur.execute(
                """
                INSERT INTO dockets (
                    docket_id, court_id, docket_number, case_name, date_filed,
                    date_terminated, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (docket_id) DO UPDATE
                SET court_id = EXCLUDED.court_id,
                    docket_number = EXCLUDED.docket_number,
                    case_name = EXCLUDED.case_name,
                    date_filed = EXCLUDED.date_filed,
                    date_terminated = EXCLUDED.date_terminated,
                    metadata = EXCLUDED.metadata
                """,
                [
                    docket_id,
                    court_id,
                    docket.get("docket_number"),
                    docket.get("case_name"),
                    docket.get("date_filed"),
                    docket.get("date_terminated"),
                    json.dumps(docket),
                ],
            )
            cur.execute(
                """
                INSERT INTO opinion_clusters (
                    cluster_id, docket_id, case_name, date_filed,
                    precedential_status, citations, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (cluster_id) DO UPDATE
                SET docket_id = EXCLUDED.docket_id,
                    case_name = EXCLUDED.case_name,
                    date_filed = EXCLUDED.date_filed,
                    precedential_status = EXCLUDED.precedential_status,
                    citations = EXCLUDED.citations,
                    metadata = EXCLUDED.metadata
                """,
                [
                    cluster_id,
                    docket_id,
                    cluster.get("case_name") or docket.get("case_name"),
                    cluster.get("date_filed") or docket.get("date_filed"),
                    cluster.get("precedential_status"),
                    json.dumps(citations),
                    json.dumps(cluster),
                ],
            )
            cur.execute(
                "SELECT content_hash FROM opinions WHERE opinion_id = %s",
                [opinion_id],
            )
            existing = cur.fetchone()
            changed = existing is None or existing[0] != content_hash
            cur.execute(
                """
                INSERT INTO opinions (
                    opinion_id, cluster_id, type, author_id, html_with_citations,
                    plain_text, sha1, source_url, source_created_at,
                    source_modified_at, content_hash, last_synced_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s::jsonb)
                ON CONFLICT (opinion_id) DO UPDATE
                SET cluster_id = EXCLUDED.cluster_id,
                    type = EXCLUDED.type,
                    author_id = EXCLUDED.author_id,
                    html_with_citations = EXCLUDED.html_with_citations,
                    plain_text = EXCLUDED.plain_text,
                    sha1 = EXCLUDED.sha1,
                    source_url = EXCLUDED.source_url,
                    source_created_at = EXCLUDED.source_created_at,
                    source_modified_at = EXCLUDED.source_modified_at,
                    content_hash = EXCLUDED.content_hash,
                    last_synced_at = now(),
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                [
                    opinion_id,
                    cluster_id,
                    opinion.get("type"),
                    resource_id(opinion.get("author") or opinion.get("author_id")),
                    html_value or None,
                    plain_value or None,
                    opinion.get("sha1"),
                    source_url,
                    opinion.get("date_created"),
                    opinion.get("date_modified"),
                    content_hash,
                    json.dumps(opinion),
                ],
            )
            chunks_created = 0
            if changed:
                cur.execute("DELETE FROM opinion_chunks WHERE opinion_id = %s", [opinion_id])
                for index, content in enumerate(chunk_text(searchable_text)):
                    cur.execute(
                        """
                        INSERT INTO opinion_chunks (
                            opinion_id, cluster_id, court_id, chunk_index, content,
                            embedding, embedding_version
                        )
                        VALUES (%s, %s, %s, %s, %s, NULL, 0)
                        """,
                        [opinion_id, cluster_id, court_id, index, content],
                    )
                    chunks_created += 1
        return 1, chunks_created

    def record_page(
        self, court_id: str, next_url: str | None, rows: int, chunks: int
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_sync_states
                SET cursor_url = %s, rows_processed = rows_processed + %s,
                    chunks_created = chunks_created + %s, updated_at = now()
                WHERE source_key = %s AND partition_key = %s
                """,
                [next_url, rows, chunks, OHIO_SOURCE_KEY, court_id],
            )
        self.conn.commit()

    def pause_run(self, run_id: Any, rows: int, chunks: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingest_runs
                SET status = 'paused', completed_at = now(), rows_processed = %s,
                    chunks_created = %s
                WHERE id = %s
                """,
                [rows, chunks, run_id],
            )
        self.conn.commit()

    def finish_partition(
        self,
        court_id: str,
        run_id: Any,
        checkpoint_at: datetime,
        rows: int,
        chunks: int,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_sync_states
                SET checkpoint_at = %s, cursor_url = NULL, status = 'idle',
                    last_successful_sync_at = now(), last_error = NULL,
                    rows_processed = 0, chunks_created = 0, updated_at = now()
                WHERE source_key = %s AND partition_key = %s
                """,
                [checkpoint_at, OHIO_SOURCE_KEY, court_id],
            )
            cur.execute(
                """
                UPDATE ingest_runs
                SET status = 'completed', completed_at = now(), rows_processed = %s,
                    chunks_created = %s
                WHERE id = %s
                """,
                [rows, chunks, run_id],
            )
            cur.execute(
                """
                UPDATE legal_sources
                SET last_successful_sync_at = now(), coverage_end = (
                        SELECT MAX(c.date_filed) FROM opinion_clusters c
                        JOIN dockets d ON d.docket_id = c.docket_id
                        JOIN courts ct ON ct.court_id = d.court_id
                        WHERE ct.jurisdiction = 'OH'
                    ),
                    item_count = (
                        SELECT COUNT(*) FROM opinions o
                        JOIN opinion_clusters c ON c.cluster_id = o.cluster_id
                        JOIN dockets d ON d.docket_id = c.docket_id
                        JOIN courts ct ON ct.court_id = d.court_id
                        WHERE ct.jurisdiction = 'OH'
                    ),
                    chunk_count = (
                        SELECT COUNT(*) FROM opinion_chunks ch
                        JOIN courts ct ON ct.court_id = ch.court_id
                        WHERE ct.jurisdiction = 'OH'
                    ),
                    embedded_chunk_count = (
                        SELECT COUNT(*) FROM opinion_chunks ch
                        JOIN courts ct ON ct.court_id = ch.court_id
                        WHERE ct.jurisdiction = 'OH' AND ch.embedding IS NOT NULL
                    ),
                    current_error = NULL, updated_at = now()
                WHERE source_key = %s
                """,
                [OHIO_SOURCE_KEY],
            )
        self.conn.commit()

    def fail_partition(self, court_id: str, run_id: Any, error: str) -> None:
        safe_error = error[:2000]
        # A page-level ingest failure leaves the transaction aborted. Roll the
        # whole page back before recording failure; the stored cursor therefore
        # remains at the last completely committed page.
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_sync_states
                SET status = 'failed', last_error = %s, updated_at = now()
                WHERE source_key = %s AND partition_key = %s
                """,
                [safe_error, OHIO_SOURCE_KEY, court_id],
            )
            cur.execute(
                """
                UPDATE ingest_runs
                SET status = 'failed', completed_at = now(), errors = %s::jsonb
                WHERE id = %s
                """,
                [json.dumps([{"court_id": court_id, "error": safe_error}]), run_id],
            )
            cur.execute(
                """
                UPDATE legal_sources
                SET current_error = %s, updated_at = now()
                WHERE source_key = %s
                """,
                [safe_error, OHIO_SOURCE_KEY],
            )
        self.conn.commit()

    def unlock_partition(self, court_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))",
                [self._source_lock_key(court_id)],
            )
        self.conn.commit()


class CourtListenerSyncer:
    def __init__(self, client: CourtListenerClient, store: SyncStore, config: SyncConfig):
        self.client = client
        self.store = store
        self.config = config

    def sync_court(self, court: dict[str, Any]) -> dict[str, Any]:
        court_id = str(court.get("id") or court.get("court_id") or "")
        if not court_id:
            raise RuntimeError("Discovered CourtListener court has no ID")
        state = self.store.start_partition(court_id)
        if state is None:
            return {"court_id": court_id, "status": "locked", "rows": 0, "chunks": 0}

        run_started = datetime.now(timezone.utc)
        run_id = None
        rows = 0
        chunks = 0
        try:
            run_id = self.store.begin_run(court_id, self.config.baseline_start)
            resume = state.get("cursor_url") if state.get("status") in {"running", "failed"} else None
            url = resume or self.client.opinions_url(
                court_id,
                baseline_start=self.config.baseline_start,
                checkpoint_at=state.get("checkpoint_at"),
                overlap_hours=self.config.overlap_hours,
            )
            pages = 0
            while url:
                page = self.client.get_json(url)
                page_rows = 0
                page_chunks = 0
                for opinion in page.get("results") or []:
                    if not isinstance(opinion, dict):
                        continue
                    bundle = self.client.opinion_bundle(court, opinion)
                    ingested, created = self.store.ingest_bundle(bundle)
                    page_rows += ingested
                    page_chunks += created
                rows += page_rows
                chunks += page_chunks
                next_url = page.get("next")
                self.store.record_page(court_id, next_url, page_rows, page_chunks)
                pages += 1
                if self.config.max_pages and pages >= self.config.max_pages and next_url:
                    self.store.pause_run(run_id, rows, chunks)
                    return {
                        "court_id": court_id,
                        "status": "paused",
                        "rows": rows,
                        "chunks": chunks,
                        "pages": pages,
                    }
                url = next_url
            self.store.finish_partition(
                court_id, run_id, run_started, rows, chunks
            )
            return {
                "court_id": court_id,
                "status": "completed",
                "rows": rows,
                "chunks": chunks,
                "pages": pages,
            }
        except Exception as exc:
            if run_id is not None:
                self.store.fail_partition(court_id, run_id, str(exc))
            raise
        finally:
            self.store.unlock_partition(court_id)

    def sync_all(self) -> list[dict[str, Any]]:
        self.store.ensure_source(self.config.baseline_start)
        return [self.sync_court(court) for court in self.client.discover_ohio_courts()]


def run_once(config: SyncConfig, db_url: str | None = None) -> list[dict[str, Any]]:
    init_schema(db_url)
    client = CourtListenerClient(config)
    try:
        with connect(db_url) as conn:
            return CourtListenerSyncer(
                client, PostgresSyncStore(conn), config
            ).sync_all()
    finally:
        client.close()


def run_scheduler(
    interval_seconds: int, config: SyncConfig | None = None, db_url: str | None = None
) -> None:
    config = config or SyncConfig.from_env()
    while True:
        try:
            results = run_once(config, db_url)
            print(json.dumps({"status": "completed", "results": results}, default=str))
        except Exception as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}))
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CourtListener Ohio baseline and incremental REST synchronizer"
    )
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--db-url")
    parser.add_argument("--baseline-start")
    parser.add_argument("--court-id", action="append", dest="court_ids")
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()

    env_config = SyncConfig.from_env()
    config = SyncConfig(
        api_key=env_config.api_key,
        api_base=env_config.api_base,
        baseline_start=(
            date.fromisoformat(args.baseline_start)
            if args.baseline_start
            else env_config.baseline_start
        ),
        overlap_hours=env_config.overlap_hours,
        timeout_seconds=env_config.timeout_seconds,
        max_pages=args.max_pages if args.max_pages is not None else env_config.max_pages,
        court_ids=tuple(args.court_ids or env_config.court_ids),
    )
    if args.once:
        print(json.dumps(run_once(config, args.db_url), indent=2, default=str))
        return
    run_scheduler(args.interval_seconds, config, args.db_url)


if __name__ == "__main__":
    main()

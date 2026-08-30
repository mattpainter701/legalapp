from __future__ import annotations

import argparse
import bz2
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from psycopg2.extras import execute_batch

from .database import connect
from .bulk_manifest import (
    choose_latest_snapshot,
    federal_appellate_court_ids,
    priority_court_ids,
)
from .schema import SCHEMA_SQL

S3_BUCKET_XML = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/?list-type=2&prefix=bulk-data/&max-keys=1000"
S3_OBJECT_URL = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/{key}"
csv.field_size_limit(min(sys.maxsize, 256 * 1024 * 1024))

DEFAULT_MVP_STATES = ("ND", "MT", "MN", "SD")
_STATE_ALIASES = {
    "ND": ("north dakota",),
    "MT": ("montana",),
    "MN": ("minnesota",),
    "SD": ("south dakota",),
}
_STATE_COURT_IDS = {
    "ND": {"nd", "ndctapp"},
    "MT": {"mont", "montag", "monttc"},
    "MN": {"minn", "minnctapp", "minnag"},
    "SD": {"sd"},
}
_REGIONAL_BANKRUPTCY_IDS = {
    "ND": {"ndb"},
    "MT": {"mtb", "bap9"},
    "MN": {"mnb", "bap8"},
    "SD": {"sdb", "bap8"},
}
_STATE_NAME_TO_CODE = {
    "north dakota": "ND",
    "dakota north": "ND",
    "montana": "MT",
    "minnesota": "MN",
    "south dakota": "SD",
    "dakota south": "SD",
}
_PRECEDENTIAL_STATUSES = {"published", "precedential"}
COVERAGE_PROFILES = ("regional", "federal-appellate", "national-priority")


def init_schema(db_url: str | None = None) -> None:
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        backfill_promoted_caselaw_snapshot(conn)
        conn.commit()


def backfill_promoted_caselaw_snapshot(conn) -> int:
    """Idempotently materialize the legacy served corpus into snapshots."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL authority.snapshot_backfill = 'on'")
        cur.execute("""
            SELECT version FROM authority_corpus_versions
            WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            cur.execute("SET LOCAL authority.snapshot_backfill = 'off'")
            return 0
        version = row[0]
        cur.execute(
            """
            INSERT INTO authority_case_clusters
              (corpus_version, cluster_id, docket_id, case_name, date_filed, citations)
            SELECT %s, cluster_id, docket_id, case_name, date_filed, citations
            FROM opinion_clusters WHERE corpus_version=%s
            ON CONFLICT (corpus_version, cluster_id) DO NOTHING
        """,
            [version, version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_opinions
              (corpus_version, opinion_id, cluster_id, source_url, plain_text)
            SELECT %s, o.opinion_id, o.cluster_id, o.source_url,
                   COALESCE(o.plain_text, o.html_with_citations)
            FROM opinions o
            JOIN opinion_clusters cl ON cl.cluster_id=o.cluster_id
                                      AND cl.corpus_version=%s
            ON CONFLICT (corpus_version, opinion_id) DO NOTHING
        """,
            [version, version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_chunks
              (corpus_version, chunk_id, opinion_id, cluster_id, court_id,
               chunk_index, content, embedding, embedding_model, embedding_version)
            SELECT %s, oc.id, oc.opinion_id, oc.cluster_id, oc.court_id,
                   oc.chunk_index, oc.content, oc.embedding, oc.embedding_model,
                   oc.embedding_version::text
            FROM opinion_chunks oc
            JOIN opinion_clusters cl ON cl.cluster_id=oc.cluster_id
                                      AND cl.corpus_version=%s
            ON CONFLICT (corpus_version, opinion_id, chunk_index) DO NOTHING
        """,
            [version, version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_citations
              (corpus_version, citing_opinion_id, cited_opinion_id,
               cited_cluster_id, cited_reporter, cited_volume, cited_page, depth)
            SELECT %s, cit.citing_opinion_id, cit.cited_opinion_id,
                   cit.cited_cluster_id, cit.cited_reporter, cit.cited_volume,
                   cit.cited_page, cit.depth
            FROM opinion_citations cit
            WHERE cit.citing_opinion_id IN (
                SELECT opinion_id FROM authority_case_opinions WHERE corpus_version=%s
            ) AND cit.cited_opinion_id IS NOT NULL
            ON CONFLICT DO NOTHING
        """,
            [version, version],
        )
        inserted = cur.rowcount
        # The INSERT exemption is migration-only and must not leak into the
        # caller's transaction, where promoted snapshots remain immutable.
        cur.execute("SET LOCAL authority.snapshot_backfill = 'off'")
        return inserted


def list_bulk_keys() -> list[str]:
    import xml.etree.ElementTree as ET

    with urllib.request.urlopen(S3_BUCKET_XML, timeout=60) as response:
        root = ET.fromstring(response.read())
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [node.text or "" for node in root.findall(".//s3:Contents/s3:Key", ns)]


def _remote_content_length(key: str) -> int | None:
    request = urllib.request.Request(S3_OBJECT_URL.format(key=key), method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        value = response.headers.get("Content-Length")
    return int(value) if value else None


def _download_object(key: str, path: Path, expected_size: int | None) -> None:
    tmp_path = path.with_name(f"{path.name}.part")
    if tmp_path.exists():
        tmp_path.unlink()
    with urllib.request.urlopen(S3_OBJECT_URL.format(key=key), timeout=300) as src:
        with tmp_path.open("wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    if expected_size is not None and tmp_path.stat().st_size != expected_size:
        actual_size = tmp_path.stat().st_size
        tmp_path.unlink(missing_ok=True)
        raise IOError(
            f"Incomplete download for {key}: expected {expected_size}, got {actual_size}"
        )
    tmp_path.replace(path)


def bz2_decompress_command(path: Path) -> list[str] | None:
    lbzip2 = shutil.which("lbzip2")
    if lbzip2:
        # CourtListener exports are large enough that the default single
        # decompressor worker leaves most of Skynet idle.  Keep the setting
        # configurable for smaller hosts, but reserve a sensible number of
        # cores by default for the PostgreSQL writer and embedding services.
        threads = max(1, int(os.getenv("COURTLISTENER_DECOMPRESS_THREADS", "8")))
        return [lbzip2, "-n", str(threads), "-dc", str(path)]
    return None


def stage_latest_snapshot(target_dir: Path) -> list[Path]:
    snapshot = choose_latest_snapshot(list_bulk_keys())
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for key in snapshot.keys:
        path = target_dir / Path(key).name
        paths.append(path)
        expected_size = _remote_content_length(key)
        if (
            path.exists()
            and expected_size is not None
            and path.stat().st_size == expected_size
        ):
            continue
        _download_object(key, path, expected_size)
    return paths


def _open_csv(path: Path):
    if path.suffix == ".bz2":
        command = bz2_decompress_command(path)
        if command:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if process.stdout is None:
                raise RuntimeError(f"Unable to read decompressor output for {path}")
            return process.stdout
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def iter_bulk_csv_rows(path: Path):
    with _open_csv(path) as handle:
        yield from csv.DictReader(handle, escapechar="\\")


def _value(row: dict, *names: str):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def best_opinion_text(row: dict) -> str | None:
    return _value(
        row,
        "plain_text",
        "html_with_citations",
        "html",
        "xml_harvard",
        "html_lawbox",
        "html_columbia",
    )


def parse_mvp_states(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_MVP_STATES
    states: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        code = part.upper() if len(part) == 2 else _STATE_NAME_TO_CODE.get(part)
        if code in _STATE_ALIASES and code not in states:
            states.append(code)
    return tuple(states) or DEFAULT_MVP_STATES


def parse_court_ids(value: str | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            part.strip().lower() for part in (value or "").split(",") if part.strip()
        )
    )


def coverage_court_ids(profile: str) -> tuple[str, ...]:
    if profile == "regional":
        return ()
    if profile == "federal-appellate":
        return federal_appellate_court_ids()
    if profile == "national-priority":
        return priority_court_ids()
    raise ValueError(f"Unsupported CourtListener coverage profile: {profile}")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _row_text(row: dict) -> str:
    return " ".join(
        str(row.get(field) or "")
        for field in ("id", "court_id", "short_name", "full_name", "jurisdiction")
    ).lower()


def _matches_state(text: str, states: tuple[str, ...]) -> bool:
    for state in states:
        for alias in _STATE_ALIASES[state]:
            if len(alias) == 2:
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return True
            elif alias in text:
                return True
    return False


def court_matches_mvp(
    row: dict,
    *,
    states: tuple[str, ...] = DEFAULT_MVP_STATES,
    include_specialty: bool = True,
    include_scotus: bool = True,
) -> bool:
    text = _row_text(row)
    court_id = str(_value(row, "id", "court_id") or "").lower()
    if include_scotus and (
        court_id == "scotus" or "supreme court of the united states" in text
    ):
        return True
    if court_id in {id_ for state in states for id_ in _STATE_COURT_IDS[state]}:
        return True
    if _matches_state(text, states) and any(
        token in text
        for token in (
            "supreme court",
            "court of appeals",
            "attorney general",
            "tax appeal board",
        )
    ):
        return True
    if not include_specialty:
        return False
    regional_bankruptcy_ids = {
        id_ for state in states for id_ in _REGIONAL_BANKRUPTCY_IDS[state]
    }
    if court_id == "tax" or "united states tax court" in text:
        return True
    if "board of immigration appeals" in text or "immigration appeals" in text:
        return True
    if court_id in regional_bankruptcy_ids:
        return True
    if "bankruptcy" in text and _matches_state(text, states):
        return True
    return False


def should_keep_cluster(row: dict, *, precedential_only: bool = True) -> bool:
    if not precedential_only:
        return True
    status = str(_value(row, "precedential_status") or "").strip().lower()
    return status in _PRECEDENTIAL_STATUSES


def resolved_table_limit(
    default_limit: int | None, table_limit: int | None
) -> int | None:
    return table_limit if table_limit is not None else default_limit


def database_size_bytes(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        row = cur.fetchone()
    return int(row[0] if row else 0)


def enforce_database_budget(conn, max_database_bytes: int | None) -> None:
    if max_database_bytes is None:
        return
    actual = database_size_bytes(conn)
    if actual >= max_database_bytes:
        raise RuntimeError(
            "CourtListener load stopped at database budget: "
            f"current={actual} bytes limit={max_database_bytes} bytes"
        )


def refresh_courtlistener_coverage_ledger(
    conn, court_ids: set[str] | None = None, source_release: str | None = None
) -> None:
    """Persist per-court observable coverage for the operator corpus inventory."""
    filters = ""
    params: list[object] = []
    if court_ids:
        filters = "WHERE d.court_id = ANY(%s)"
        params.append(sorted(court_ids))
    with conn.cursor() as cur:
        if source_release is None:
            cur.execute("""
                SELECT version FROM authority_corpus_versions
                WHERE status IN ('staged', 'canary', 'promoted')
                ORDER BY CASE status WHEN 'staged' THEN 0 WHEN 'canary' THEN 1 ELSE 2 END,
                         created_at DESC
                LIMIT 1
            """)
            release_row = cur.fetchone()
            source_release = release_row[0] if release_row else None
        if source_release is None:
            return
        cur.execute(
            f"""
            INSERT INTO corpus_coverage_ledger (
                source_key, partition_key, expected_coverage, acquisition_state,
                source_release,
                rows_loaded, chunks_loaded, vectors_loaded, first_document_date,
                last_document_date, last_checked_at, metadata, updated_at
            )
            SELECT 'courtlistener:ohio-caselaw', d.court_id,
                   jsonb_build_object('court_id', d.court_id, 'source', 'CourtListener bulk'),
                   CASE WHEN COUNT(ch.id) = 0 THEN 'loading' ELSE 'partial' END,
                   %s,
                   COUNT(DISTINCT o.opinion_id), COUNT(ch.id),
                   COUNT(ch.id) FILTER (WHERE ch.embedding IS NOT NULL),
                   MIN(oc.date_filed), MAX(oc.date_filed), now(),
                   jsonb_build_object('dockets', COUNT(DISTINCT d.docket_id),
                                      'clusters', COUNT(DISTINCT oc.cluster_id)), now()
            FROM dockets d
            LEFT JOIN opinion_clusters oc ON oc.docket_id = d.docket_id
            LEFT JOIN opinions o ON o.cluster_id = oc.cluster_id
            LEFT JOIN opinion_chunks ch ON ch.opinion_id = o.opinion_id
            {filters}
            GROUP BY d.court_id
            ON CONFLICT (source_key, partition_key, source_release) DO UPDATE SET
                rows_loaded = EXCLUDED.rows_loaded,
                chunks_loaded = EXCLUDED.chunks_loaded,
                vectors_loaded = EXCLUDED.vectors_loaded,
                first_document_date = EXCLUDED.first_document_date,
                last_document_date = EXCLUDED.last_document_date,
                last_checked_at = EXCLUDED.last_checked_at,
                acquisition_state = EXCLUDED.acquisition_state,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            """,
            [source_release, *params],
        )


def _target_court_ids(
    conn,
    *,
    states: tuple[str, ...],
    include_specialty: bool,
    include_scotus: bool,
    additional_court_ids: tuple[str, ...] = (),
) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT court_id, short_name, full_name, jurisdiction, metadata FROM courts"
        )
        rows = cur.fetchall()
    ids: set[str] = set()
    for court_id, short_name, full_name, jurisdiction, metadata in rows:
        row = dict(metadata or {})
        row.update(
            {
                "id": court_id,
                "short_name": short_name,
                "full_name": full_name,
                "jurisdiction": jurisdiction,
            }
        )
        if str(court_id).lower() in additional_court_ids or court_matches_mvp(
            row,
            states=states,
            include_specialty=include_specialty,
            include_scotus=include_scotus,
        ):
            ids.add(str(court_id))
    return ids


def _docket_ids_for_courts(conn, court_ids: set[str]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT docket_id FROM dockets WHERE court_id = ANY(%s)",
            [sorted(court_ids)],
        )
        return {str(row[0]) for row in cur.fetchall()}


def _cluster_ids_for_courts(
    conn,
    court_ids: set[str],
    *,
    precedential_only: bool,
) -> set[str]:
    status_clause = (
        "AND lower(COALESCE(cl.precedential_status, '')) = ANY(%s)"
        if precedential_only
        else ""
    )
    params: list = [sorted(court_ids)]
    if precedential_only:
        params.append(sorted(_PRECEDENTIAL_STATUSES))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT cl.cluster_id
               FROM opinion_clusters cl
               JOIN dockets d ON d.docket_id = cl.docket_id
               WHERE d.court_id = ANY(%s)
               {status_clause}""",
            params,
        )
        return {str(row[0]) for row in cur.fetchall()}


def _opinion_ids_for_courts(
    conn,
    court_ids: set[str],
    *,
    precedential_only: bool,
) -> set[str]:
    status_clause = (
        "AND lower(COALESCE(cl.precedential_status, '')) = ANY(%s)"
        if precedential_only
        else ""
    )
    params: list = [sorted(court_ids)]
    if precedential_only:
        params.append(sorted(_PRECEDENTIAL_STATUSES))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT o.opinion_id
               FROM opinions o
               JOIN opinion_clusters cl ON cl.cluster_id = o.cluster_id
               JOIN dockets d ON d.docket_id = cl.docket_id
               WHERE d.court_id = ANY(%s)
               {status_clause}""",
            params,
        )
        return {str(row[0]) for row in cur.fetchall()}


def _existing_ids(conn, table_name: str, id_column: str) -> set[str]:
    """Return the stable primary keys already present in a bulk table.

    Bulk snapshots are deterministic and are read from their beginning on each
    run.  A bounded follow-on tranche must therefore explicitly skip the rows
    already persisted, otherwise its row limit is consumed by conflict updates
    before it reaches the next court partition.
    """
    allowed = {
        ("dockets", "docket_id"),
        ("opinion_clusters", "cluster_id"),
        ("opinions", "opinion_id"),
    }
    if (table_name, id_column) not in allowed:
        raise ValueError(f"Unsupported existing-ID lookup: {table_name}.{id_column}")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {id_column} FROM {table_name}")
        return {str(row[0]) for row in cur.fetchall()}


def _load_csv(
    conn,
    path: Path,
    table_name: str,
    limit: int | None = None,
    row_filter: Callable[[dict], bool] | None = None,
    max_database_bytes: int | None = None,
    budget_check_every: int = 1000,
    write_batch_size: int | None = None,
) -> int:
    if limit is not None and limit <= 0:
        return 0
    batch_size = write_batch_size or max(
        1, int(os.getenv("COURTLISTENER_WRITE_BATCH_SIZE", "500"))
    )
    count = 0
    pending: list[list[object]] = []

    statements = {
        "courts": """
            INSERT INTO courts (court_id, short_name, full_name, jurisdiction, metadata)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (court_id) DO UPDATE
            SET short_name = EXCLUDED.short_name,
                full_name = EXCLUDED.full_name,
                jurisdiction = EXCLUDED.jurisdiction,
                metadata = EXCLUDED.metadata
        """,
        "dockets": """
            INSERT INTO dockets (docket_id, court_id, docket_number, case_name, date_filed, date_terminated, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (docket_id) DO NOTHING
        """,
        "opinion_clusters": """
            INSERT INTO opinion_clusters (cluster_id, docket_id, case_name, date_filed, precedential_status, citations, metadata, corpus_version)
            VALUES (%s, %s, %s, %s, %s, COALESCE(%s::jsonb, '[]'::jsonb), %s::jsonb, %s)
            -- A cluster identity is not allowed to mutate an older release.
            -- Versioned snapshots are materialized before promotion.
            ON CONFLICT (cluster_id) DO NOTHING
        """,
        "opinions": """
            INSERT INTO opinions (opinion_id, cluster_id, type, author_id, html_with_citations, plain_text, sha1, source_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (opinion_id) DO UPDATE
            SET plain_text = EXCLUDED.plain_text,
                html_with_citations = EXCLUDED.html_with_citations
            WHERE opinions.plain_text IS DISTINCT FROM EXCLUDED.plain_text
               OR opinions.html_with_citations IS DISTINCT FROM EXCLUDED.html_with_citations
        """,
        "citation_map": """
            INSERT INTO opinion_citations (citing_opinion_id, cited_opinion_id, depth)
            SELECT %s, %s, COALESCE(%s, 0)
            WHERE NOT EXISTS (
                SELECT 1 FROM opinion_citations
                WHERE citing_opinion_id = %s AND cited_opinion_id = %s
            )
        """,
        "citations": """
            INSERT INTO opinion_citations (cited_cluster_id, cited_reporter, cited_volume, cited_page)
            SELECT %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM opinion_citations
                WHERE cited_cluster_id = %s
                  AND cited_reporter IS NOT DISTINCT FROM %s
                  AND cited_volume IS NOT DISTINCT FROM %s
                  AND cited_page IS NOT DISTINCT FROM %s
            )
        """,
    }
    corpus_version = None
    if table_name == "opinion_clusters":
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM authority_corpus_versions WHERE status IN ('staged','canary') ORDER BY created_at DESC LIMIT 1"
            )
            version_row = cur.fetchone()
        if not version_row:
            raise PermissionError(
                "caselaw loading requires a staged or canary corpus version"
            )
        corpus_version = version_row[0]
    if table_name not in statements:
        raise ValueError(f"Unsupported bulk table: {table_name}")

    def parameters(row: dict) -> list[object]:
        if table_name == "courts":
            return [
                _value(row, "id", "court_id"),
                _value(row, "short_name"),
                _value(row, "full_name") or _value(row, "id"),
                _value(row, "jurisdiction"),
                json.dumps(row),
            ]
        if table_name == "dockets":
            return [
                _value(row, "id"),
                _value(row, "court_id", "court"),
                _value(row, "docket_number"),
                _value(row, "case_name"),
                _value(row, "date_filed"),
                _value(row, "date_terminated"),
                json.dumps(row),
            ]
        if table_name == "opinion_clusters":
            citations = _value(row, "citations")
            return [
                _value(row, "id"),
                _value(row, "docket_id"),
                _value(row, "case_name"),
                _value(row, "date_filed"),
                _value(row, "precedential_status"),
                citations if citations else None,
                json.dumps(row),
                corpus_version,
            ]
        if table_name == "opinions":
            return [
                _value(row, "id"),
                _value(row, "cluster_id"),
                _value(row, "type"),
                _value(row, "author_id"),
                _value(row, "html_with_citations", "html", "xml_harvard"),
                _value(row, "plain_text"),
                _value(row, "sha1"),
                _value(row, "download_url", "local_path"),
            ]
        if table_name == "citation_map":
            citing_id, cited_id = (
                _value(row, "citing_opinion_id"),
                _value(row, "cited_opinion_id"),
            )
            return [citing_id, cited_id, _value(row, "depth"), citing_id, cited_id]
        cluster_id = _value(row, "cluster_id")
        reporter, volume, page = (
            _value(row, "reporter"),
            _value(row, "volume"),
            _value(row, "page"),
        )
        return [cluster_id, reporter, volume, page, cluster_id, reporter, volume, page]

    with conn.cursor() as cur:

        def flush() -> None:
            nonlocal count
            if not pending:
                return
            batch_values = list(pending)
            # execute_batch turns hundreds of client/server round trips into a
            # single request while preserving every table's existing conflict
            # and idempotency behavior.  The fallback keeps lightweight test
            # cursors and non-psycopg adapters compatible.
            if hasattr(cur, "mogrify"):
                execute_batch(
                    cur, statements[table_name], pending, page_size=len(pending)
                )
            else:
                for values in pending:
                    cur.execute(statements[table_name], values)
            if table_name == "opinion_clusters":
                for values in batch_values:
                    cur.execute(
                        """INSERT INTO authority_case_clusters
                      (corpus_version, cluster_id, docket_id, case_name, date_filed, citations)
                      VALUES (%s, %s, %s, %s, %s, COALESCE(%s::jsonb, '[]'::jsonb))
                      ON CONFLICT (corpus_version, cluster_id) DO UPDATE SET
                        docket_id=EXCLUDED.docket_id, case_name=EXCLUDED.case_name,
                        date_filed=EXCLUDED.date_filed, citations=EXCLUDED.citations""",
                        [
                            values[-1],
                            values[0],
                            values[1],
                            values[2],
                            values[3],
                            values[5],
                        ],
                    )
            elif table_name == "opinions":
                for values in batch_values:
                    cur.execute(
                        """INSERT INTO authority_case_opinions
                      (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                      SELECT cl.corpus_version, %s, %s, %s, COALESCE(%s, %s)
                      FROM authority_case_clusters cl
                      WHERE cl.cluster_id=%s
                        AND cl.corpus_version=%s
                      ON CONFLICT (corpus_version, opinion_id) DO UPDATE SET
                        cluster_id=EXCLUDED.cluster_id,
                        source_url=EXCLUDED.source_url, plain_text=EXCLUDED.plain_text""",
                        [
                            values[0],
                            values[1],
                            values[7],
                            values[5],
                            values[4],
                            values[1],
                            corpus_version,
                        ],
                    )
            count += len(pending)
            pending.clear()
            if count % budget_check_every == 0:
                enforce_database_budget(conn, max_database_bytes)
            conn.commit()

        for row in iter_bulk_csv_rows(path):
            if row_filter and not row_filter(row):
                continue
            pending.append(parameters(row))
            if len(pending) >= batch_size:
                flush()
            if limit is not None and count + len(pending) >= limit:
                break
        flush()
    enforce_database_budget(conn, max_database_bytes)
    return count


def load_staged_core(
    bulk_dir: Path, db_url: str | None = None, limit: int | None = None
) -> dict[str, int]:
    load_order = [
        ("courts-*.csv.bz2", "courts"),
        ("dockets-*.csv.bz2", "dockets"),
        ("opinion-clusters-*.csv.bz2", "opinion_clusters"),
        ("opinions-*.csv.bz2", "opinions"),
        ("citations-*.csv.bz2", "citations"),
        ("citation-map-*.csv.bz2", "citation_map"),
    ]
    counts: dict[str, int] = {}
    with connect(db_url) as conn:
        for pattern, table_name in load_order:
            matches = sorted(bulk_dir.glob(pattern))
            if not matches:
                continue
            counts[table_name] = _load_csv(conn, matches[-1], table_name, limit=limit)
    return counts


def load_mvp_corpus(
    bulk_dir: Path,
    db_url: str | None = None,
    limit: int | None = None,
    docket_limit: int | None = None,
    cluster_limit: int | None = None,
    opinion_limit: int | None = None,
    citation_limit: int | None = None,
    states: tuple[str, ...] = DEFAULT_MVP_STATES,
    include_specialty: bool = True,
    include_scotus: bool = True,
    precedential_only: bool = True,
    additional_court_ids: tuple[str, ...] = (),
    max_database_bytes: int | None = None,
    skip_existing: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect(db_url) as conn:
        courts = sorted(bulk_dir.glob("courts-*.csv.bz2"))
        if courts:
            counts["courts"] = _load_csv(conn, courts[-1], "courts")

        target_courts = _target_court_ids(
            conn,
            states=states,
            include_specialty=include_specialty,
            include_scotus=include_scotus,
            additional_court_ids=additional_court_ids,
        )
        if not target_courts:
            raise RuntimeError(f"No MVP CourtListener courts matched states={states!r}")
        counts["target_courts"] = len(target_courts)

        dockets = sorted(bulk_dir.glob("dockets-*.csv.bz2"))
        if dockets:
            existing_docket_ids = (
                _existing_ids(conn, "dockets", "docket_id") if skip_existing else set()
            )
            counts["dockets"] = _load_csv(
                conn,
                dockets[-1],
                "dockets",
                limit=resolved_table_limit(limit, docket_limit),
                row_filter=lambda row: (
                    str(_value(row, "court_id", "court") or "") in target_courts
                    and str(_value(row, "id") or "") not in existing_docket_ids
                ),
                max_database_bytes=max_database_bytes,
            )
        docket_ids = _docket_ids_for_courts(conn, target_courts)

        clusters = sorted(bulk_dir.glob("opinion-clusters-*.csv.bz2"))
        if clusters:
            existing_cluster_ids = (
                _existing_ids(conn, "opinion_clusters", "cluster_id")
                if skip_existing
                else set()
            )
            counts["opinion_clusters"] = _load_csv(
                conn,
                clusters[-1],
                "opinion_clusters",
                limit=resolved_table_limit(limit, cluster_limit),
                row_filter=lambda row: (
                    str(_value(row, "docket_id") or "") in docket_ids
                    and str(_value(row, "id") or "") not in existing_cluster_ids
                    and should_keep_cluster(row, precedential_only=precedential_only)
                ),
                max_database_bytes=max_database_bytes,
            )
        cluster_ids = _cluster_ids_for_courts(
            conn,
            target_courts,
            precedential_only=precedential_only,
        )

        opinions = sorted(bulk_dir.glob("opinions-*.csv.bz2"))
        if opinions:
            existing_opinion_ids = (
                _existing_ids(conn, "opinions", "opinion_id")
                if skip_existing
                else set()
            )
            counts["opinions"] = _load_csv(
                conn,
                opinions[-1],
                "opinions",
                limit=resolved_table_limit(limit, opinion_limit),
                row_filter=lambda row: (
                    str(_value(row, "cluster_id") or "") in cluster_ids
                    and str(_value(row, "id") or "") not in existing_opinion_ids
                ),
                max_database_bytes=max_database_bytes,
            )
        opinion_ids = _opinion_ids_for_courts(
            conn,
            target_courts,
            precedential_only=precedential_only,
        )

        citations = sorted(bulk_dir.glob("citations-*.csv.bz2"))
        if citations:
            counts["citations"] = _load_csv(
                conn,
                citations[-1],
                "citations",
                limit=resolved_table_limit(limit, citation_limit),
                row_filter=lambda row: str(_value(row, "cluster_id") or "")
                in cluster_ids,
                max_database_bytes=max_database_bytes,
            )

        citation_maps = sorted(bulk_dir.glob("citation-map-*.csv.bz2"))
        if citation_maps:
            counts["citation_map"] = _load_csv(
                conn,
                citation_maps[-1],
                "citation_map",
                limit=resolved_table_limit(limit, citation_limit),
                row_filter=lambda row: (
                    str(_value(row, "citing_opinion_id") or "") in opinion_ids
                    and str(_value(row, "cited_opinion_id") or "") in opinion_ids
                ),
                max_database_bytes=max_database_bytes,
            )
        refresh_courtlistener_coverage_ledger(conn, target_courts)
        conn.commit()
    return counts


def chunk_text(text: str, max_chars: int = 2400, overlap_chars: int = 240) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap_chars)
    return chunks


def create_chunks(db_url: str | None = None, limit: int | None = None) -> int:
    created = 0
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.opinion_id, o.cluster_id, d.court_id, cl.corpus_version,
                       COALESCE(NULLIF(o.plain_text, ''), NULLIF(o.html_with_citations, '')) AS text
                FROM opinions o
                JOIN opinion_clusters cl ON cl.cluster_id = o.cluster_id
                LEFT JOIN dockets d ON d.docket_id = cl.docket_id
                WHERE cl.corpus_version IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM opinion_chunks oc WHERE oc.opinion_id = o.opinion_id
                      AND oc.corpus_version = cl.corpus_version
                )
                ORDER BY o.opinion_id
                LIMIT %s
                """,
                [limit or 1000000],
            )
            rows = cur.fetchall()
            for opinion_id, cluster_id, court_id, corpus_version, text in rows:
                for idx, content in enumerate(chunk_text(text or "")):
                    cur.execute(
                        """
                        INSERT INTO opinion_chunks (opinion_id, cluster_id, court_id, chunk_index, content, corpus_version)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (opinion_id, chunk_index) DO NOTHING
                        """,
                        [
                            opinion_id,
                            cluster_id,
                            court_id,
                            idx,
                            content,
                            corpus_version,
                        ],
                    )
                    created += cur.rowcount
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM authority_corpus_versions WHERE status IN ('staged','canary') ORDER BY created_at DESC LIMIT 1"
            )
            version_row = cur.fetchone()
        if version_row:
            # Snapshot-backed serving must not depend on the singleton legacy
            # opinion_chunks table.  Generate candidate chunks directly from
            # the candidate opinion snapshot, then copy any legacy metadata
            # that is still needed for compatibility.
            created += create_snapshot_chunks(conn, version_row[0], limit=limit)
            materialize_caselaw_snapshot(conn, version_row[0])
        refresh_courtlistener_coverage_ledger(
            conn, source_release=version_row[0] if version_row else None
        )
        conn.commit()
    return created


def materialize_caselaw_snapshot(conn, corpus_version: str) -> None:
    """Copy one staged caselaw release into immutable version-keyed tables."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO authority_case_clusters
              (corpus_version, cluster_id, docket_id, case_name, date_filed, citations)
            SELECT corpus_version, cluster_id, docket_id, case_name, date_filed, citations
            FROM opinion_clusters WHERE corpus_version=%s
            ON CONFLICT (corpus_version, cluster_id) DO NOTHING
        """,
            [corpus_version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_opinions
              (corpus_version, opinion_id, cluster_id, source_url, plain_text)
            SELECT %s, o.opinion_id, o.cluster_id, o.source_url,
                   COALESCE(o.plain_text, o.html_with_citations)
            FROM opinions o JOIN opinion_clusters cl ON cl.cluster_id=o.cluster_id
            WHERE cl.corpus_version=%s
            ON CONFLICT (corpus_version, opinion_id) DO NOTHING
        """,
            [corpus_version, corpus_version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_chunks
              (corpus_version, chunk_id, opinion_id, cluster_id, court_id,
               chunk_index, content, embedding, embedding_model, embedding_version)
            SELECT %s, oc.id, oc.opinion_id, oc.cluster_id, oc.court_id,
                   oc.chunk_index, oc.content, oc.embedding, oc.embedding_model,
                   oc.embedding_version::text
            FROM opinion_chunks oc JOIN opinion_clusters cl ON cl.cluster_id=oc.cluster_id
            WHERE cl.corpus_version=%s AND oc.corpus_version=%s
            ON CONFLICT (corpus_version, opinion_id, chunk_index) DO UPDATE SET
              content=EXCLUDED.content, embedding=EXCLUDED.embedding,
              embedding_model=EXCLUDED.embedding_model,
              embedding_version=EXCLUDED.embedding_version
        """,
            [corpus_version, corpus_version, corpus_version],
        )
        cur.execute(
            """
            INSERT INTO authority_case_citations
              (corpus_version, citing_opinion_id, cited_opinion_id,
               cited_cluster_id, cited_reporter, cited_volume, cited_page, depth)
            SELECT %s, cit.citing_opinion_id, cit.cited_opinion_id,
                   cit.cited_cluster_id, cit.cited_reporter, cit.cited_volume,
                   cit.cited_page, cit.depth
            FROM opinion_citations cit
            WHERE EXISTS (SELECT 1 FROM authority_case_opinions ao
                          WHERE ao.corpus_version=%s AND ao.opinion_id=cit.citing_opinion_id)
            ON CONFLICT DO NOTHING
        """,
            [corpus_version, corpus_version],
        )


def create_snapshot_chunks(conn, corpus_version: str, limit: int | None = None) -> int:
    """Materialize text chunks solely within one staged snapshot.

    Legacy opinion/opinion_cluster rows have singleton identities and cannot
    represent two releases safely.  Snapshot opinions are therefore the
    authoritative chunking input for a staged release.
    """
    created = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ao.opinion_id, ac.cluster_id, d.court_id, ao.plain_text
            FROM authority_case_opinions ao
            JOIN authority_case_clusters ac
              ON ac.corpus_version=ao.corpus_version
             AND ac.cluster_id=ao.cluster_id
            LEFT JOIN dockets d ON d.docket_id=ac.docket_id
            WHERE ao.corpus_version=%s
            ORDER BY ao.opinion_id
            LIMIT %s
            """,
            [corpus_version, limit or 1000000],
        )
        for opinion_id, cluster_id, court_id, text in cur.fetchall():
            cur.execute(
                "DELETE FROM authority_case_chunks WHERE corpus_version=%s AND opinion_id=%s",
                [corpus_version, opinion_id],
            )
            for idx, content in enumerate(chunk_text(text or "")):
                cur.execute(
                    """
                    INSERT INTO authority_case_chunks
                      (corpus_version, opinion_id, cluster_id, court_id,
                       chunk_index, content)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (corpus_version, opinion_id, chunk_index)
                    DO UPDATE SET content=EXCLUDED.content
                    """,
                    [corpus_version, opinion_id, cluster_id, court_id, idx, content],
                )
                created += cur.rowcount
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="CourtListener MCP bulk loader")
    parser.add_argument("--init-schema", action="store_true")
    parser.add_argument("--stage-latest", action="store_true")
    parser.add_argument("--load-staged", action="store_true")
    parser.add_argument("--load-mvp", action="store_true")
    parser.add_argument("--chunk-opinions", action="store_true")
    parser.add_argument("--bulk-dir", default="/data/courtlistener")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--docket-limit", type=int)
    parser.add_argument("--cluster-limit", type=int)
    parser.add_argument("--opinion-limit", type=int)
    parser.add_argument("--citation-limit", type=int)
    parser.add_argument(
        "--max-database-gb", type=float, help="Stop before this logical database size"
    )
    parser.add_argument("--db-url")
    parser.add_argument(
        "--mvp-states",
        default=os.getenv("COURTLISTENER_MVP_STATES", ",".join(DEFAULT_MVP_STATES)),
    )
    parser.add_argument(
        "--coverage-profile",
        choices=COVERAGE_PROFILES,
        default=os.getenv("COURTLISTENER_COVERAGE_PROFILE", "regional"),
        help="add federal appellate or national-priority courts to the regional set",
    )
    parser.add_argument(
        "--court-id",
        action="append",
        dest="court_ids",
        help="repeatable CourtListener court ID to add to the selected profile",
    )
    parser.add_argument(
        "--no-specialty",
        action="store_true",
        default=not _env_bool("COURTLISTENER_MVP_SPECIALTY", True),
    )
    parser.add_argument(
        "--no-scotus",
        action="store_true",
        default=not _env_bool("COURTLISTENER_MVP_SCOTUS", True),
    )
    parser.add_argument("--include-unpublished", action="store_true")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip existing bulk IDs so a bounded follow-on tranche reaches new rows",
    )
    args = parser.parse_args()
    if args.init_schema:
        init_schema(args.db_url)
    if args.stage_latest:
        paths = stage_latest_snapshot(Path(args.bulk_dir))
        print("\n".join(str(path) for path in paths))
    if args.load_staged:
        print(load_staged_core(Path(args.bulk_dir), args.db_url, args.limit))
    if args.load_mvp:
        print(
            load_mvp_corpus(
                Path(args.bulk_dir),
                args.db_url,
                args.limit,
                docket_limit=args.docket_limit,
                cluster_limit=args.cluster_limit,
                opinion_limit=args.opinion_limit,
                citation_limit=args.citation_limit,
                states=parse_mvp_states(args.mvp_states),
                include_specialty=not args.no_specialty,
                include_scotus=not args.no_scotus,
                precedential_only=not args.include_unpublished,
                additional_court_ids=tuple(
                    dict.fromkeys(
                        (
                            *coverage_court_ids(args.coverage_profile),
                            *parse_court_ids(
                                os.getenv("COURTLISTENER_EXTRA_COURT_IDS")
                            ),
                            *(court_id.lower() for court_id in (args.court_ids or [])),
                        )
                    )
                ),
                max_database_bytes=(
                    int(args.max_database_gb * 1024**3)
                    if args.max_database_gb
                    else None
                ),
                skip_existing=args.skip_existing,
            )
        )
    if args.chunk_opinions:
        print({"chunks_created": create_chunks(args.db_url, args.limit)})
    if not (
        args.init_schema
        or args.stage_latest
        or args.load_staged
        or args.load_mvp
        or args.chunk_opinions
    ):
        raise SystemExit(
            "Use --init-schema, --stage-latest, --load-staged, --load-mvp, or --chunk-opinions"
        )


if __name__ == "__main__":
    main()

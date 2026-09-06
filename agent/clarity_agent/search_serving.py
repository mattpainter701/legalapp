"""OpenSearch text sink and portal adapter over the durable scan manifest.

The inherited SQLite store contains jobs, ACL evidence and status only: the
text-publish hook writes exclusively to OpenSearch. A ready manifest version
and fresh native access decision are required before releasing each hit.
"""

from __future__ import annotations

import asyncio
import tempfile
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

from clarity_agent.local_index import (
    LocalSearchIndex,
    PermanentIndexError,
    _relative_under_root,
)
from clarity_agent.native_acl import authorize_acl
from clarity_agent.search_engine import DocumentMutation, SearchFilters, SearchRequest
from clarity_agent.search_ingest import document_chunks


class OpenSearchServingIndex(LocalSearchIndex):
    def __init__(self, db_path, engine, *, extractor_settings=None, **kwargs):
        super().__init__(db_path, **kwargs)
        from search_node.config import Settings
        from search_node.extraction import IsolatedExtractor
        from clarity_agent.search_control import SqliteControlState

        self.engine = engine
        self.extractor = IsolatedExtractor(extractor_settings or Settings.from_env())
        self.extractor.settings.assert_worker_safe()
        self.generations = SqliteControlState(
            str(Path(db_path).with_suffix(".generations.db"))
        )

    async def init(self):
        await asyncio.to_thread(self.extractor.preflight)
        await self.generations.init()
        await super().init()
        if self.available:
            await self._db.execute(
                "CREATE TABLE IF NOT EXISTS pending_engine_deletions (document_id TEXT PRIMARY KEY, generation INTEGER NOT NULL)"
            )
            await self._db.commit()
            await self._drain_deletions()

    async def close(self):
        await super().close()
        await self.generations.close()

    async def _extract_text(self, job, content):
        from search_node.contracts import ManifestJob
        from clarity_agent.config import _restrict

        settings = self.extractor.settings
        settings.staging_root.mkdir(parents=True, exist_ok=True)
        _restrict(settings.staging_root, required=True)
        with tempfile.TemporaryDirectory(
            prefix="portal-", dir=settings.staging_root
        ) as directory:
            path = Path(directory) / ("source" + job["ext"])
            path.write_bytes(content)
            _restrict(path, required=True)
            work = ManifestJob(
                job_id=str(job["_claim_lease_until"]),
                document_id=job["path"],
                source_id=job["share_id"],
                file_id=job["path"],
                content_version=job["content_hash"] or "unknown",
                lease_token=str(job["_claim_lease_until"]),
                share_id=job["share_id"],
                source_path=str(path),
                relative_path=job["path"],
                content_fingerprint=hashlib.sha256(content).hexdigest(),
                pipeline_version="portal-v1",
                size_bytes=len(content),
            )
            record = await asyncio.to_thread(self.extractor.extract, work)
        if str(record.status) != "indexed-ready":
            raise PermanentIndexError(str(record.status))
        job["ocr_pending"] = bool(record.ocr_pending_pages)
        if record.ocr_pending_pages and not record.sections:
            raise PermanentIndexError("ocr_pending")
        return [(s.page_number, s.ordinal, s.text) for s in record.sections], None

    async def _publish_text(self, job, rows, acl_record):
        identity = hashlib.sha256(
            f"{job['share_id']}:{job['path'].casefold()}".encode()
        ).hexdigest()
        generation = await self.generations.next_mutation_generation(identity)
        version = hashlib.sha256(
            f"{job['path']}:{job['content_hash']}:{generation}".encode()
        ).hexdigest()
        if acl_record.get("state") != "healthy":
            raise PermanentIndexError("acl_unavailable")
        assigned = await self._path_validator(job)
        if not assigned:
            raise PermanentIndexError("path_outside_assigned_share")
        # Paths stay on premises. The response adapter constructs a relative
        # path only after checking the latest assignment and matter scope.
        record = SimpleNamespace(
            status="indexed-ready",
            document_id=identity,
            share_id=job["share_id"],
            relative_path=job["path"],
            filename=PureWindowsPath(job["path"]).name,
            extension=job["ext"],
            content_version=version,
            content_fingerprint=job["content_hash"] or version,
            matter_ids=(),
            acl_state="healthy",
            acl_tokens=tuple(
                str(ace["sid"]).upper() for ace in acl_record.get("allow", [])
            ),
            sections=tuple(
                SimpleNamespace(
                    text=text,
                    ordinal=ordinal,
                    page_number=page,
                    chunk_id=f"{version}:{ordinal}",
                    section_path=(),
                    start_offset=None,
                    end_offset=None,
                )
                for page, ordinal, text in rows
            ),
        )
        modified = datetime.fromisoformat(
            str(job["modified_time"]).replace("Z", "+00:00")
        )
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        chunks = document_chunks(
            record,
            modified_at=modified,
            mutation_generation=generation,
            deny_acl_tokens=tuple(
                str(ace["sid"]).upper() for ace in acl_record.get("deny", [])
            ),
        )
        result = await self.engine.bulk_index(chunks)
        if result.failed_ids or result.accepted != len(chunks):
            raise RuntimeError("opensearch_publish_incomplete")
        return {
            **acl_record,
            "document_version": version,
            "ocr_pending": bool(job.get("ocr_pending")),
        }

    async def delete(self, paths):
        if not self.available:
            return
        async with self._db_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                for path in paths:
                    cursor = await self._db.execute(
                        "SELECT share_id FROM index_files WHERE path=?", (path,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        identity = hashlib.sha256(
                            f"{row['share_id']}:{path.casefold()}".encode()
                        ).hexdigest()
                        generation = await self.generations.next_mutation_generation(
                            identity
                        )
                        await self._db.execute(
                            "INSERT OR REPLACE INTO pending_engine_deletions VALUES (?,?)",
                            (identity, generation),
                        )
                        await self._db.execute(
                            "DELETE FROM index_files WHERE path=?", (path,)
                        )
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise
        # The durable local denial/outbox commit precedes the remote mutation.
        # Every successful scan retries this, even with no newly deleted paths.
        await self._drain_deletions()

    async def _drain_deletions(self):
        async with self._db_lock:
            cursor = await self._db.execute(
                "SELECT * FROM pending_engine_deletions LIMIT 1000"
            )
            rows = await cursor.fetchall()
            for row in rows:
                try:
                    await self.engine.delete_documents(
                        [DocumentMutation(row["document_id"], row["generation"])]
                    )
                except RuntimeError as exc:
                    # A newer document may already have superseded this delete.
                    # The engine's generation fence proves it must be retained.
                    if str(exc) != "document mutation generation is stale":
                        raise
                await self._db.execute(
                    "DELETE FROM pending_engine_deletions WHERE document_id=? AND generation=?",
                    (row["document_id"], row["generation"]),
                )
            await self._db.commit()

    async def authorize_path(self, path, authorization, *, acl_max_age_seconds=3600):
        # Keep manifest/deletion/source fencing, then re-read the actual DACL.
        decision = await super().authorize_path(
            path,
            authorization,
            acl_max_age_seconds=acl_max_age_seconds,
        )
        if not decision.allowed:
            return decision
        try:
            async with self._db_lock:
                cursor = await self._db.execute(
                    "SELECT * FROM index_files WHERE path=? AND status='ready'", (path,)
                )
                row = await cursor.fetchone()
            record = self._acl_loader(dict(row)) if row else None
            if inspect.isawaitable(record):
                record = await record
        except Exception:
            record = None
        return authorize_acl(
            record, authorization.principal_sids, max_age_seconds=acl_max_age_seconds
        )

    async def stats(self):
        result = await super().stats()
        if self.available and self._db:
            async with self._db_lock:
                cursor = await self._db.execute(
                    "SELECT count(*) FROM index_files WHERE json_extract(acl_json, '$.ocr_pending')=1"
                )
                count = int((await cursor.fetchone())[0])
            if count:
                result["statuses"]["ocr_pending"] = {"files": count, "source_bytes": 0}
        return result

    async def search(
        self,
        query,
        scopes,
        assigned_shares,
        extensions,
        limit,
        authorization=None,
        acl_max_age_seconds=3600,
    ):
        if authorization is None:
            raise ValueError("OpenSearch portal search requires native authorization")
        if not self.available or not self._db:
            raise RuntimeError("OpenSearch manifest unavailable")
        if not scopes or len(scopes) > 100:
            raise ValueError("bounded assigned scopes are required")
        assigned = {str(share.get("share_id")): share for share in assigned_shares}
        roots = []
        for scope in scopes:
            share_id = str(scope.get("share_id") or "")
            if share_id not in assigned or share_id not in authorization.source_ids:
                raise ValueError("search scope is not authorized")
            root = (
                str(assigned[share_id].get("share_path") or "")
                .replace("/", "\\")
                .rstrip("\\")
            )
            folder = str(scope.get("folder_path") or "").replace("/", "\\").strip("\\")
            if not root or any(part in {".", ".."} for part in folder.split("\\")):
                raise ValueError("invalid assigned folder")
            roots.append((share_id, root + ("\\" + folder if folder else "")))
        limit = max(1, min(int(limit), 100))
        response = await self.engine.search(
            SearchRequest(
                query=query,
                acl_tokens=tuple(authorization.principal_sids),
                filters=SearchFilters(
                    path_scopes=tuple(roots),
                    share_ids=tuple(dict.fromkeys(s for s, _ in roots)),
                    extensions=tuple(
                        "." + e.lstrip(".").lower() for e in (extensions or [])
                    ),
                ),
                limit=min(100, max(limit * 2, limit)),
            )
        )
        hits = []
        filtered = False
        for hit in response.hits:
            path = hit.relative_path
            share = assigned.get(hit.share_id)
            if share is None or not any(
                hit.share_id == sid and _relative_under_root(root, path)
                for sid, root in roots
            ):
                filtered = True
                continue
            async with self._db_lock:
                cursor = await self._db.execute(
                    "SELECT acl_json FROM index_files WHERE path=? AND share_id=? AND status='ready'",
                    (path, hit.share_id),
                )
                row = await cursor.fetchone()
            stored = json.loads(row["acl_json"] or "null") if row else None
            if (
                not stored
                or not hit.document_version
                or stored.get("document_version") != hit.document_version
            ):
                filtered = True
                continue
            decision = await self.authorize_path(
                path, authorization, acl_max_age_seconds=acl_max_age_seconds
            )
            if not decision.allowed:
                filtered = True
                continue
            hits.append(
                {
                    "share_id": hit.share_id,
                    "relative_path": _relative_under_root(share["share_path"], path),
                    "filename": PureWindowsPath(path).name,
                    "ext": PureWindowsPath(path).suffix.lower(),
                    "snippet": hit.snippet[:1000],
                    "page_number": hit.page_number,
                    "score": hit.score,
                }
            )
            if len(hits) >= limit:
                break
        stats = await self.stats()
        statuses = stats["statuses"]
        ready = statuses.get("ready", {}).get("files", 0)
        pending = sum(
            statuses.get(state, {}).get("files", 0) for state in ("pending", "running")
        )
        incomplete = (
            pending
            or filtered
            or any(
                statuses.get(state, {}).get("files", 0)
                for state in ("error", "unsupported", "ocr_pending")
            )
        )
        return {
            "hits": hits,
            "index_state": "partial" if incomplete else "ready" if ready else "empty",
            "indexed_files": ready,
            "pending_files": pending,
        }

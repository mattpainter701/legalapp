"""Executes the work the SaaS queues for this agent.

Four task kinds arrive on the same poll:

* ``content_fetch`` — read one file and return its text (on-demand retrieval
  for chat/RAG context; the SaaS only ever stores snippets otherwise);
* ``verify_share`` — mount a share with its configured credential and report
  whether it worked, which is what the admin console's "Test connection"
  button waits on;
* ``scan_now`` — run an immediate scan instead of waiting for the schedule.
* ``agent_update`` — check/apply the fixed official release (never a task URL).
* ``local_search`` — search the agent-local private lexical index.
* ``authorize_file`` — revalidate native ACLs before a name/preview/open release.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import smbclient

from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.smb_auth import ShareCredential, connect, session_kwargs
from clarity_agent.smb_reader import SmbReader
from clarity_agent.search_identity import (
    IdentityTicketError,
    ReplayCache,
    verify_search_identity_ticket,
)
from clarity_agent.utils import normalize_unc_path, parse_smb_path

logger = logging.getLogger("clarity_agent.tasks")

# How many entries a connection test lists before reporting success. Enough to
# prove the mount and read permission without walking a large share.
VERIFY_SAMPLE_LIMIT = 25


class _LocalSearchInputError(ValueError):
    """A fixed, query-free task validation error safe to return and log."""


class TaskWorker:
    def __init__(
        self,
        config: AgentConfig,
        client: SaaSClient,
        reader: SmbReader,
        share_provider=None,
        scan_callback=None,
        share_refresher=None,
        update_callback=None,
        local_search_index=None,
    ):
        self.config = config
        self.client = client
        self.reader = reader
        # Returns the current share list (with credentials) from the SaaS.
        self.share_provider = share_provider
        # Runs a full scan of one share; used by ``scan_now``.
        self.scan_callback = scan_callback
        # Forces a re-fetch of that list, for a share added moments ago.
        self.share_refresher = share_refresher
        self.update_callback = update_callback
        # Search remains fail-closed when the optional private index is absent
        # or failed to initialize.
        self.local_search_index = local_search_index
        self._ticket_replays = ReplayCache()

    def _authorization_for_task(self, task: dict, source_ids: set[str]):
        ticket = str(task.get("identity_ticket") or "")
        if not getattr(self.config, "native_authz_enabled", False) and not ticket:
            return None
        public_key = getattr(self.config, "search_identity_public_key", "")
        if not public_key:
            raise IdentityTicketError("native authorization key is unavailable")
        authorization = verify_search_identity_ticket(
            ticket,
            public_key=public_key,
            audience=str(self.config.agent_id),
            tenant_id=str(task.get("tenant_id") or ""),
            required_source_ids=source_ids,
            replay_cache=self._ticket_replays,
        )
        expected_filters = {
            key: task[key]
            for key in ("matter_id", "file_id")
            if task.get(key) is not None
        }
        if task.get("kind") == "local_search":
            expected_filters["file_extensions"] = sorted(
                {
                    str(value).lower().lstrip(".")
                    for value in task.get("file_extensions") or []
                }
            )
        for key, expected in expected_filters.items():
            actual = authorization.filters.get(key)
            if key == "file_extensions":
                actual = sorted(
                    {str(value).lower().lstrip(".") for value in actual or []}
                )
            if actual != expected:
                raise IdentityTicketError("identity ticket filter scope mismatch")
        return authorization

    async def poll_and_execute(self) -> int:
        try:
            tasks = await self.client.get_tasks()
        except Exception as exc:
            logger.error("Failed to fetch tasks: %s", exc)
            return -1

        if not tasks:
            return 0

        processed = 0
        for task in tasks:
            try:
                await self._execute_task(task)
                processed += 1
            except Exception as exc:
                logger.error("Task %s failed: %s", task.get("task_id", "?"), exc)

        return processed

    async def _execute_task(self, task: dict) -> None:
        kind = task.get("kind") or "content_fetch"
        if kind == "verify_share":
            await self._verify_share(task)
        elif kind == "scan_now":
            await self._scan_now(task)
        elif kind == "content_fetch":
            await self._fetch_content(task)
        elif kind == "agent_update":
            await self._update_agent(task)
        elif kind == "local_search":
            await self._local_search(task)
        elif kind == "authorize_file":
            await self._authorize_file(task)
        else:
            await self.client.submit_task_result(
                task["task_id"], ok=False, error=f"Unsupported task kind: {kind}"
            )

    async def _local_search(self, task: dict) -> None:
        """Execute a bounded, privacy-safe search against the local index."""
        task_id = task.get("task_id")
        correlation_id = task.get("correlation_id")
        query = task.get("query")
        scopes = task.get("scopes")
        extensions = task.get("file_extensions")
        limit = task.get("limit", 20)

        def reject(message: str) -> None:
            # Keep query text and scope paths out of both logs and errors.
            raise _LocalSearchInputError(message)

        try:
            if not isinstance(task_id, str) or not task_id:
                reject("Missing task_id")
            if not isinstance(correlation_id, str) or not correlation_id.strip():
                reject("correlation_id is required")
            if len(correlation_id) > 128:
                reject("correlation_id must be at most 128 characters")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", correlation_id):
                reject("correlation_id contains invalid characters")
            if not isinstance(query, str) or not query.strip():
                reject("query is required")
            if len(query) > 1000:
                reject("query must be at most 1000 characters")
            if not isinstance(scopes, list) or not scopes:
                reject("at least one search scope is required")
            if (
                not isinstance(limit, int)
                or isinstance(limit, bool)
                or not 1 <= limit <= 50
            ):
                reject("limit must be between 1 and 50")
            if extensions is not None and not isinstance(extensions, list):
                reject("file_extensions must be a list")
            if extensions is not None and len(extensions) > 50:
                reject("file_extensions is too large")
            if extensions is not None and any(
                not isinstance(extension, str)
                or not re.fullmatch(r"\.?[A-Za-z0-9][A-Za-z0-9_-]{0,18}", extension)
                for extension in extensions
            ):
                reject("file_extensions contains an invalid extension")
            for scope in scopes:
                if not isinstance(scope, dict) or not scope.get("share_id"):
                    reject("each search scope requires a share_id")
                folder = scope.get("folder_path", "")
                if not isinstance(folder, str):
                    reject("folder_path must be text")
                parts = [
                    part
                    for part in folder.replace("\\", "/").split("/")
                    if part and part != "."
                ]
                if (
                    any(part == ".." for part in parts)
                    or ":" in folder
                    or folder.startswith(("/", "\\"))
                ):
                    reject("search scope is outside its assigned share")

            assigned = await self._shares()
            assigned_ids = {str(share.get("share_id")) for share in assigned}
            if any(str(scope["share_id"]) not in assigned_ids for scope in scopes):
                reject("search scope is not assigned to this agent")
            source_ids = {str(scope["share_id"]) for scope in scopes}
            try:
                authorization = self._authorization_for_task(task, source_ids)
            except IdentityTicketError as exc:
                reject(str(exc))

            started = time.perf_counter()
            index = self.local_search_index
            if index is None or not getattr(index, "available", False):
                await self.client.submit_task_result(
                    task_id,
                    ok=False,
                    error="Local search index is unavailable",
                    detail=self._search_detail(
                        correlation_id, [], "unavailable", 0, 0, started
                    ),
                )
                logger.warning(
                    "Local search unavailable correlation_id=%s", correlation_id
                )
                return
            if authorization is None:
                # Preserve the pre-gate index contract for deployments and
                # test doubles while rollout remains explicitly disabled.
                result = await index.search(query, scopes, assigned, extensions, limit)
            else:
                result = await index.search(
                    query,
                    scopes,
                    assigned,
                    extensions,
                    limit,
                    authorization=authorization,
                    acl_max_age_seconds=getattr(
                        self.config, "acl_max_age_seconds", 3600
                    ),
                )
            allowed = {
                "relative_path",
                "filename",
                "ext",
                "snippet",
                "page_number",
                "score",
                "share_id",
            }
            hits = [
                {key: hit[key] for key in allowed if key in hit}
                for hit in result.get("hits", [])[:limit]
                if isinstance(hit, dict)
            ]
            detail = self._search_detail(
                correlation_id,
                hits,
                result.get("index_state", "unknown"),
                result.get("indexed_files", 0),
                result.get("pending_files", 0),
                started,
            )
            await self.client.submit_task_result(task_id, ok=True, detail=detail)
            logger.info(
                "Local search completed correlation_id=%s hits=%d indexed_files=%d pending_files=%d duration_ms=%d",
                correlation_id,
                len(hits),
                detail["indexed_files"],
                detail["pending_files"],
                detail["duration_ms"],
            )
        except _LocalSearchInputError as exc:
            await self.client.submit_task_result(task_id, ok=False, error=str(exc))
            logger.warning(
                "Local search rejected correlation_id=%s reason=%s",
                correlation_id or "?",
                str(exc),
            )
        except Exception as exc:
            await self.client.submit_task_result(
                task_id, ok=False, error="Local search failed"
            )
            logger.error(
                "Local search failed correlation_id=%s error_type=%s",
                correlation_id or "?",
                type(exc).__name__,
            )

    @staticmethod
    def _search_detail(
        correlation_id, hits, index_state, indexed_files, pending_files, started
    ):
        return {
            "schema_version": 1,
            "correlation_id": correlation_id,
            "hits": hits,
            "index_state": index_state,
            "indexed_files": int(indexed_files or 0),
            "pending_files": int(pending_files or 0),
            "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
        }

    async def _update_agent(self, task: dict) -> None:
        task_id = task["task_id"]
        if self.update_callback is None:
            await self.client.submit_task_result(
                task_id, ok=False, error="Agent update support is unavailable"
            )
            return
        # Portal updates are apply-only. URLs, asset names, and a caller-
        # controlled dry-run flag are intentionally not accepted from tasks.
        target_version = task.get("target_version")
        if not target_version:
            await self.client.submit_task_result(
                task_id, ok=False, error="Missing target_version in agent_update task"
            )
            return
        manifest_id = task.get("manifest_id")
        if manifest_id != f"agent-v{target_version}":
            await self.client.submit_task_result(
                task_id, ok=False, error="Invalid manifest_id in agent_update task"
            )
            return
        try:
            result = await self.update_callback(target_version, manifest_id)
            await self.client.submit_task_result(task_id, ok=True, detail=result)
        except Exception as exc:
            await self.client.submit_task_result(task_id, ok=False, error=str(exc))

    # ── content fetch ───────────────────────────────────────────────────────

    async def _authorize_file(self, task: dict) -> None:
        """Return only an authorization decision; never file metadata/content."""
        task_id = task.get("task_id")
        try:
            file_path = str(task.get("file_path") or "").strip()
            share_id = str(task.get("share_id") or "")
            share = await self._share_for_path(file_path, share_id)
            if share is None or self.local_search_index is None:
                raise ValueError("native ACL state is unavailable")
            authorization = self._authorization_for_task(task, {share_id})
            if authorization is None:
                raise ValueError("native authorization is disabled")
            decision = await self.local_search_index.authorize_path(
                file_path,
                authorization,
                acl_max_age_seconds=getattr(self.config, "acl_max_age_seconds", 3600),
            )
            await self.client.submit_task_result(
                task_id,
                ok=decision.allowed,
                error=None if decision.allowed else "Native authorization denied",
                detail={"authorized": decision.allowed, "reason": decision.reason},
            )
        except Exception as exc:
            logger.warning(
                "Native file authorization failed task_id=%s error_type=%s",
                task_id or "?",
                type(exc).__name__,
            )
            await self.client.submit_task_result(
                task_id, ok=False, error="Native authorization denied"
            )

    async def _fetch_content(self, task: dict) -> None:
        task_id = task["task_id"]
        file_path = (task.get("file_path") or "").strip()

        if not file_path:
            await self.client.submit_task_result(
                task_id, error="Missing file_path in task"
            )
            return

        try:
            share = await self._share_for_path(file_path, task.get("share_id"))
            if share is None:
                raise ValueError("File path is not under an assigned share")
            authorization = self._authorization_for_task(
                task, {str(task.get("share_id") or "")}
            )
            if authorization is not None:
                if self.local_search_index is None:
                    raise ValueError("native ACL state is unavailable")
                decision = await self.local_search_index.authorize_path(
                    file_path,
                    authorization,
                    acl_max_age_seconds=getattr(
                        self.config, "acl_max_age_seconds", 3600
                    ),
                )
                if not decision.allowed:
                    raise ValueError("native authorization denied")
            credential = ShareCredential.from_share(share, self.config)
            server, _, _ = parse_smb_path(file_path)
            connection_cache: dict = {}
            connection_kwargs = {
                **session_kwargs(credential),
                "connection_cache": connection_cache,
            }
            await asyncio.to_thread(
                connect,
                server,
                credential,
                smbclient_module=smbclient,
                connection_cache=connection_cache,
            )

            result = await self.reader.read_content(
                None, file_path, connection_kwargs=connection_kwargs
            )
            if result.error:
                await self.client.submit_task_result(task_id, error=result.error)
            else:
                await self.client.submit_task_result(
                    task_id, content=result.content, truncated=result.truncated
                )
        except Exception as exc:
            logger.error(
                "Content fetch failed task_id=%s error_type=%s",
                task_id,
                type(exc).__name__,
            )
            error = (
                "Content fetch denied"
                if getattr(self.config, "native_authz_enabled", False)
                else str(exc)
            )
            await self.client.submit_task_result(task_id, error=error)
        finally:
            if "connection_cache" in locals():
                await asyncio.to_thread(
                    smbclient.reset_connection_cache,
                    connection_cache=connection_cache,
                    fail_on_error=False,
                )

    # ── connection test ─────────────────────────────────────────────────────

    async def _verify_share(self, task: dict) -> None:
        task_id = task["task_id"]
        share_id = task.get("share_id")
        share = await self._share_by_id(share_id)
        share_path = (share or {}).get("share_path") or task.get("share_path") or ""

        if not share_path:
            await self.client.submit_task_result(
                task_id, ok=False, error="Share is not assigned to this agent"
            )
            return

        credential = ShareCredential.from_share(share or {}, self.config)
        try:
            server, share_name, root = parse_smb_path(share_path)
        except ValueError as exc:
            await self.client.submit_task_result(task_id, ok=False, error=str(exc))
            return

        connection_cache: dict = {}
        try:
            await asyncio.to_thread(
                connect,
                server,
                credential,
                smbclient_module=smbclient,
                connection_cache=connection_cache,
            )
            connection_kwargs = {
                **session_kwargs(credential),
                "connection_cache": connection_cache,
            }
            probe = share_path.rstrip("\\")
            names = await asyncio.to_thread(
                _sample_share_entries,
                smbclient,
                probe,
                VERIFY_SAMPLE_LIMIT,
                connection_kwargs,
            )
            await self.client.submit_task_result(
                task_id,
                ok=True,
                detail={
                    "identity": credential.describe,
                    "server": server,
                    "share": share_name,
                    "root_path": root or "",
                    "entries_sampled": len(names),
                },
            )
            logger.info("Verified %s as %s", share_path, credential.describe)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.error("Verification of %s failed: %s", share_path, message)
            await self.client.submit_task_result(
                task_id,
                ok=False,
                error=message,
                detail={"identity": credential.describe, "server": server},
            )
        finally:
            await asyncio.to_thread(
                smbclient.reset_connection_cache,
                connection_cache=connection_cache,
                fail_on_error=False,
            )

    # ── scan now ────────────────────────────────────────────────────────────

    async def _scan_now(self, task: dict) -> None:
        task_id = task["task_id"]
        share = await self._share_by_id(task.get("share_id"))
        if share is None:
            await self.client.submit_task_result(
                task_id, ok=False, error="Share is not assigned to this agent"
            )
            return
        if self.scan_callback is None:
            await self.client.submit_task_result(
                task_id, ok=False, error="Agent is not running its scan loop"
            )
            return

        try:
            outcome = await self.scan_callback(share) or {}
            detail = {
                "share_path": share.get("share_path", ""),
                "status": outcome.get("status", "success"),
                "file_count": outcome.get("file_count"),
            }
            # A scan can fail without raising (no route to the share, a sync
            # rejection); report what it recorded rather than "finished".
            if outcome.get("status") in ("failed", "error", "partial"):
                await self.client.submit_task_result(
                    task_id,
                    ok=False,
                    error=outcome.get("error") or f"Scan {outcome['status']}",
                    detail=detail,
                )
            else:
                await self.client.submit_task_result(task_id, ok=True, detail=detail)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.error("Scan-now for %s failed: %s", share.get("share_path"), message)
            await self.client.submit_task_result(task_id, ok=False, error=message)

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _shares(self) -> list[dict]:
        if self.share_provider is None:
            return []
        try:
            return await self.share_provider()
        except Exception as exc:
            logger.error("Could not load shares: %s", exc)
            return []

    async def _share_by_id(self, share_id: str | None) -> dict | None:
        if not share_id:
            return None
        for share in await self._shares():
            if str(share.get("share_id")) == str(share_id):
                return share

        # An admin can add a share and test it seconds later, before the cached
        # share list expires, so a miss is worth one forced refresh.
        if self.share_refresher is not None:
            try:
                shares = await self.share_refresher()
            except Exception as exc:
                logger.error("Could not refresh shares: %s", exc)
                return None
            for share in shares or []:
                if str(share.get("share_id")) == str(share_id):
                    return share
        return None

    async def _share_for_path(
        self, file_path: str, share_id: str | None = None
    ) -> dict | None:
        """Find the share a file lives under, so the right credential is used."""
        if share_id:
            found = await self._share_by_id(share_id)
            if found and self._path_is_under_share(file_path, found.get("share_path")):
                return found
            if found:
                return None
        try:
            target = normalize_unc_path(file_path).casefold()
        except ValueError:
            return None
        best = None
        for share in await self._shares():
            try:
                prefix = normalize_unc_path(share.get("share_path") or "").casefold()
            except ValueError:
                continue
            if target == prefix or target.startswith(prefix + "\\"):
                # Longest matching prefix wins when shares are nested.
                if best is None or len(prefix) > len(best.get("share_path") or ""):
                    best = share
        return best

    @staticmethod
    def _path_is_under_share(file_path: str, share_path: str | None) -> bool:
        try:
            target = normalize_unc_path(file_path).casefold()
            prefix = normalize_unc_path(share_path or "").casefold()
        except ValueError:
            return False
        return target == prefix or target.startswith(prefix + "\\")


def _sample_share_entries(
    smbclient_module, path: str, limit: int, connection_kwargs: dict
) -> list[str]:
    """List only a bounded sample while SMB I/O remains off the event loop."""
    names = []
    for entry in smbclient_module.scandir(path, **connection_kwargs):
        names.append(entry.name)
        if len(names) >= limit:
            break
    return names

"""Executes the work the SaaS queues for this agent.

Four task kinds arrive on the same poll:

* ``content_fetch`` — read one file and return its text (on-demand retrieval
  for chat/RAG context; the SaaS only ever stores snippets otherwise);
* ``verify_share`` — mount a share with its configured credential and report
  whether it worked, which is what the admin console's "Test connection"
  button waits on;
* ``scan_now`` — run an immediate scan instead of waiting for the schedule.
* ``agent_update`` — check/apply the fixed official release (never a task URL).
"""

from __future__ import annotations

import asyncio
import logging

import smbclient

from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.smb_auth import ShareCredential, connect, session_kwargs
from clarity_agent.smb_reader import SmbReader
from clarity_agent.utils import normalize_unc_path, parse_smb_path

logger = logging.getLogger("clarity_agent.tasks")

# How many entries a connection test lists before reporting success. Enough to
# prove the mount and read permission without walking a large share.
VERIFY_SAMPLE_LIMIT = 25


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
        else:
            await self.client.submit_task_result(
                task["task_id"], ok=False, error=f"Unsupported task kind: {kind}"
            )

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
            logger.error("Content fetch for %s failed: %s", file_path, exc)
            await self.client.submit_task_result(task_id, error=str(exc))
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

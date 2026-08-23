"""Executes the work the SaaS queues for this agent.

Three task kinds arrive on the same poll:

* ``content_fetch`` — read one file and return its text (on-demand retrieval
  for chat/RAG context; the SaaS only ever stores snippets otherwise);
* ``verify_share`` — mount a share with its configured credential and report
  whether it worked, which is what the admin console's "Test connection"
  button waits on;
* ``scan_now`` — run an immediate scan instead of waiting for the schedule.
"""

from __future__ import annotations

import logging

import smbclient

from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.smb_auth import ShareCredential, connect
from clarity_agent.smb_reader import SmbReader
from clarity_agent.utils import parse_smb_path

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
    ):
        self.config = config
        self.client = client
        self.reader = reader
        # Returns the current share list (with credentials) from the SaaS.
        self.share_provider = share_provider
        # Runs a full scan of one share; used by ``scan_now``.
        self.scan_callback = scan_callback

    async def poll_and_execute(self) -> int:
        try:
            tasks = await self.client.get_tasks()
        except Exception as exc:
            logger.error("Failed to fetch tasks: %s", exc)
            return 0

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
        else:
            await self._fetch_content(task)

    # ── content fetch ───────────────────────────────────────────────────────

    async def _fetch_content(self, task: dict) -> None:
        task_id = task["task_id"]
        file_path = task.get("file_path", "")

        if not file_path:
            await self.client.submit_task_result(
                task_id, error="Missing file_path in task"
            )
            return

        try:
            share = await self._share_for_path(file_path, task.get("share_id"))
            credential = ShareCredential.from_share(share or {}, self.config)
            server, _, _ = parse_smb_path(file_path)
            smbclient.reset_connection_cache()
            connect(server, credential, smbclient_module=smbclient)

            result = await self.reader.read_content(None, file_path)
            if result.error:
                await self.client.submit_task_result(task_id, error=result.error)
            else:
                await self.client.submit_task_result(
                    task_id, content=result.content, truncated=result.truncated
                )
        except Exception as exc:
            logger.error("Content fetch for %s failed: %s", file_path, exc)
            await self.client.submit_task_result(task_id, error=str(exc))

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

        try:
            smbclient.reset_connection_cache()
            connect(server, credential, smbclient_module=smbclient)
            probe = share_path.rstrip("\\")
            names = []
            for entry in smbclient.scandir(probe):
                names.append(entry.name)
                if len(names) >= VERIFY_SAMPLE_LIMIT:
                    break
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
            await self.scan_callback(share)
            await self.client.submit_task_result(
                task_id, ok=True, detail={"share_path": share.get("share_path", "")}
            )
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
        return None

    async def _share_for_path(
        self, file_path: str, share_id: str | None = None
    ) -> dict | None:
        """Find the share a file lives under, so the right credential is used."""
        if share_id:
            found = await self._share_by_id(share_id)
            if found:
                return found
        target = file_path.replace("/", "\\").lower()
        best = None
        for share in await self._shares():
            prefix = (share.get("share_path") or "").replace("/", "\\").lower()
            if prefix and target.startswith(prefix.rstrip("\\")):
                # Longest matching prefix wins when shares are nested.
                if best is None or len(prefix) > len(best.get("share_path") or ""):
                    best = share
        return best

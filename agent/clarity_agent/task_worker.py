from __future__ import annotations

import logging

import smbclient

from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.smb_reader import SmbReader

logger = logging.getLogger("clarity_agent.tasks")


class TaskWorker:
    def __init__(self, config: AgentConfig, client: SaaSClient, reader: SmbReader):
        self.config = config
        self.client = client
        self.reader = reader

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
        task_id = task["task_id"]
        file_path = task.get("file_path", "")
        share_server = task.get("share_server", "")
        share_name = task.get("share_name", "")

        if not file_path:
            await self.client.submit_task_result(task_id, content="", error="Missing file_path in task")
            return

        try:
            smbclient.reset_connection_cache()
            if share_server:
                smbclient.register_session(
                    share_server,
                    username=self.config.smb_username,
                    password=self.config.smb_password,
                    domain=self.config.smb_domain or None,
                )

            result = await self.reader.read_content(None, file_path)
            if result.error:
                await self.client.submit_task_result(
                    task_id, content="", error=result.error
                )
            else:
                await self.client.submit_task_result(
                    task_id, content=result.content, truncated=result.truncated
                )
        except Exception as exc:
            logger.error("Content fetch for %s failed: %s", file_path, exc)
            await self.client.submit_task_result(
                task_id, content="", error=str(exc)
            )
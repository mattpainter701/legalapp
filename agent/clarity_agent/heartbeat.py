from __future__ import annotations

import logging
import platform
import socket

from clarity_agent import __version__
from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent.updater import read_update_status

logger = logging.getLogger("clarity_agent.heartbeat")


def host_info() -> dict:
    """Identity fields the SaaS shows in the agent list."""
    return {
        "agent_version": __version__,
        "hostname": socket.gethostname(),
        "os_info": platform.platform(),
    }


class HeartbeatService:
    def __init__(self, config: AgentConfig, client: SaaSClient):
        self.config = config
        self.client = client

    async def send(self, active_scans: int = 0) -> None:
        # Keys match AgentHeartbeatRequest; anything else is dropped by the API,
        # which is why the version column used to stay empty.
        info = host_info()
        payload = {
            "agent_version": info["agent_version"],
            "hostname": info["hostname"],
            "active_scans": active_scans,
        }
        update_status = read_update_status()
        if update_status:
            payload["update_status"] = update_status["status"]
            payload["update_target_version"] = update_status["target_version"]
            if update_status.get("error"):
                payload["update_error"] = update_status["error"]
        try:
            await self.client.heartbeat(payload)
            logger.debug("Heartbeat sent")
        except Exception as exc:
            logger.warning("Heartbeat failed: %s", exc)

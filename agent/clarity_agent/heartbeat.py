from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone

from clarity_agent.api_client import SaaSClient
from clarity_agent.config import AgentConfig
from clarity_agent import __version__

logger = logging.getLogger("clarity_agent.heartbeat")


class HeartbeatService:
    def __init__(self, config: AgentConfig, client: SaaSClient):
        self.config = config
        self.client = client

    async def send(self, active_scans: int = 0) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "version": __version__,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "active_scans": active_scans,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            await self.client.heartbeat(payload)
            logger.debug("Heartbeat sent")
        except Exception as exc:
            logger.warning("Heartbeat failed: %s", exc)
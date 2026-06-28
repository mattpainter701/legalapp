"""Compatibility entrypoint for CourtListener Jetson embedding workers.

The implementation lives in ``mcp-server/mcp_server/jetson_worker.py`` so the
Docker MCP stack and the bare-metal Jetson runner share one worker contract.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from mcp_server.jetson_worker import main


if __name__ == "__main__":
    main()

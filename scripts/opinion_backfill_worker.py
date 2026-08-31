"""Compatibility entrypoint for the durable CourtListener staging worker."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from mcp_server.opinion_backfill import main  # noqa: E402


if __name__ == "__main__":
    main()

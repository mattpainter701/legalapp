"""Entrypoint for the opt-in, dedicated Studio render worker process."""

from __future__ import annotations

import asyncio
import signal

from app.config import get_settings
from app.services.studio_render_runtime import build_studio_render_worker_loop


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, name, None)
        if value is not None:
            try:
                loop.add_signal_handler(value, stop.set)
            except NotImplementedError:
                pass
    worker = build_studio_render_worker_loop(get_settings())
    await worker.run_forever(stop)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

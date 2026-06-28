from __future__ import annotations

import argparse
import time


def run_scheduler(interval_seconds: int) -> None:
    while True:
        # Placeholder for bounded REST incremental sync. Keep this process
        # explicit rather than silently crawling CourtListener under low quotas.
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="CourtListener low-volume REST sync scheduler")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()
    run_scheduler(args.interval_seconds)


if __name__ == "__main__":
    main()

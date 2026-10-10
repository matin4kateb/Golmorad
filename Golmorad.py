"""Golmorad knowledge-base updater entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from pathlib import Path


def _load_local_env() -> None:
    """Load simple KEY=VALUE settings without overwriting the process environment."""
    env_file = Path(__file__).resolve().with_name(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    _load_local_env()
    logging.basicConfig(
        level=os.getenv("APP_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Update Golmorad's Bitcoin knowledge base")
    parser.add_argument("--once", action="store_true", help="run one update cycle and exit")
    parser.add_argument("--interval", type=int,
                        default=int(os.getenv("DATA_REFRESH_INTERVAL_SECONDS", "900")),
                        help="seconds between background update cycles (minimum 60)")
    args = parser.parse_args()

    from Database.DataSeeder import DataSeeder

    seeder = DataSeeder()
    if args.once:
        print(seeder.run_once())
        return 0

    stopped = threading.Event()

    def stop_handler(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_handler)
    seeder.start(args.interval)
    logging.getLogger(__name__).info("Knowledge-base refresh started; interval=%ds", args.interval)
    stopped.wait()
    seeder.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json

from unaufhaltsam_youtube.config import Config
from unaufhaltsam_youtube.daily_loop import run_daily_performance_loop
from unaufhaltsam_youtube.database import Database


def main() -> int:
    config = Config.from_env()
    print(json.dumps(run_daily_performance_loop(config, Database(config.db_file)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

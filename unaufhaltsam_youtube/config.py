"""Environment-driven autopilot configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .paths import DB_FILE, OUTPUT_DIR
from .runtime import read_runtime


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} muss eine ganze Zahl sein") from exc
    if value < minimum:
        raise ValueError(f"{name} muss mindestens {minimum} sein")
    return value


@dataclass(frozen=True)
class Config:
    game_url: str
    public_game_url: str
    publish_mode: str
    max_runs_per_day: int
    max_uploads_per_day: int
    minimum_publish_score: int
    cooldown_minutes: int
    browser_retries: int
    run_timeout_seconds: int
    strategy: str
    output_dir: Path
    db_file: Path
    max_queue_size: int

    @classmethod
    def from_env(cls) -> "Config":
        mode = os.environ.get("PUBLISH_MODE", read_runtime()["publish_mode"]).lower()
        if mode not in {"dry_run", "public"}:
            raise ValueError("PUBLISH_MODE muss dry_run oder public sein")
        output = Path(os.environ.get("OUTPUT_DIR", OUTPUT_DIR)).resolve()
        return cls(
            game_url=os.environ.get(
                "GAME_URL", "https://focus-system-unaufhaltsam.github.io/eddie/"
            ),
            public_game_url="https://eddiesworld.de/unaufhaltsam/",
            publish_mode=mode,
            max_runs_per_day=_integer("MAX_RUNS_PER_DAY", 20, 1),
            max_uploads_per_day=_integer("MAX_UPLOADS_PER_DAY", 1, 1),
            minimum_publish_score=_integer("MINIMUM_PUBLISH_SCORE", 10, 0),
            cooldown_minutes=_integer("COOLDOWN_MINUTES", 30, 0),
            browser_retries=_integer("BROWSER_RETRIES", 2, 0),
            run_timeout_seconds=_integer("RUN_TIMEOUT_SECONDS", 180, 10),
            strategy=os.environ.get("BOT_STRATEGY", "visual_dom_tracking"),
            output_dir=output,
            db_file=Path(os.environ.get("AUTOPILOT_DB", DB_FILE)).resolve(),
            max_queue_size=_integer("MAX_QUEUE_SIZE", 50, 1),
        )

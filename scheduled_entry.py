"""Non-interactive Windows Task Scheduler entry point."""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = OUTPUT_DIR / "logs"
LOG_FILE = LOG_DIR / "scheduler.log"


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _line(message: str) -> None:
    print(f"[{_timestamp()}] {message}", flush=True)


def main() -> int:
    os.chdir(PROJECT_ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.pop("PUBLISH_MODE", None)
    os.environ["MAX_RUNS_PER_DAY"] = "20"
    os.environ["MAX_UPLOADS_PER_DAY"] = "1"
    os.environ["MINIMUM_PUBLISH_SCORE"] = "10"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        Path("C:/Users/kesch/AppData/Local/ms-playwright")
    )

    with LOG_FILE.open("a", encoding="utf-8", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                _line("START: Autopilot wird im persistent konfigurierten Modus gestartet.")
                from unaufhaltsam_youtube.autopilot import main as autopilot_main

                exit_code = autopilot_main(["--once"])
                if exit_code:
                    _line(f"FEHLER: Autopilot endete mit Exit-Code {exit_code}.")
                    return exit_code
                _line("ENDE: Autopilot erfolgreich abgeschlossen.")
                return 0
            except Exception as exc:
                _line(f"FEHLER: {exc}")
                return 1


if __name__ == "__main__":
    raise SystemExit(main())

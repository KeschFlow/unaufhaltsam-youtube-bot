"""Python wrapper around the Playwright/Chromium runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Config
from .models import RunResult


class BrowserGameError(RuntimeError):
    pass


def run_bot(config: Config, attempt_id: str) -> RunResult:
    if config.strategy != "visual_v1":
        raise BrowserGameError("FAIRNESS_FAIL: DOM-Identitaetsstrategie gesperrt")
    recordings = config.output_dir / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("browser_runner.mjs")
    command = [
        "node", str(script), f"--attempt={attempt_id}", f"--output={recordings}",
        f"--url={config.game_url}", f"--timeout={config.run_timeout_seconds}",
        f"--strategy={config.strategy}",
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            timeout=config.run_timeout_seconds + 90, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserGameError(
            f"Browser-Runner hat das Zeitlimit von "
            f"{config.run_timeout_seconds + 90} Sekunden ueberschritten"
        ) from exc
    if process.returncode != 0:
        raise BrowserGameError(
            f"Browser-Runner fehlgeschlagen ({process.returncode}): "
            f"{process.stderr[-2000:]}"
        )
    diagnostic_log = config.output_dir / "logs" / "browser-runner.jsonl"
    diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
    if process.stderr:
        with diagnostic_log.open("a", encoding="utf-8") as log:
            log.write(process.stderr)
    try:
        payload = json.loads(process.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise BrowserGameError(f"Ungueltige Runner-Ausgabe: {process.stdout[-1000:]}") from exc
    result = RunResult.from_dict(payload)
    result.validate()
    log_path = config.output_dir / "logs" / "bot-runs.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")
    return result

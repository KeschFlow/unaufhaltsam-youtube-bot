"""Atomic persistent runtime mode used by scheduler and live switch."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .paths import PROJECT_ROOT

RUNTIME_FILE = PROJECT_ROOT / "config" / "runtime.json"


def read_runtime() -> dict:
    defaults = {"publish_mode": "dry_run", "analytics_mode": "dry_run"}
    if not RUNTIME_FILE.is_file():
        return defaults
    payload = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    defaults.update(payload)
    if defaults["publish_mode"] not in {"dry_run", "public"}:
        raise ValueError("Persistenter publish_mode ist ungueltig")
    if defaults["analytics_mode"] not in {"dry_run", "api"}:
        raise ValueError("Persistenter analytics_mode ist ungueltig")
    return defaults


def set_publish_mode(mode: str) -> Path:
    if mode not in {"dry_run", "public"}:
        raise ValueError("publish_mode muss dry_run oder public sein")
    payload = read_runtime()
    payload["publish_mode"] = mode
    RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=RUNTIME_FILE.parent, prefix=".runtime-", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, RUNTIME_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    return RUNTIME_FILE

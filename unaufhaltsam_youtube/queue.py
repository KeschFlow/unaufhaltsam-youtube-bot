"""Persistent filesystem queue for locally processed attempts."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def queue_item_path(output_dir: Path, attempt_id: str) -> Path:
    return output_dir / "queue" / f"{attempt_id}.json"


def write_queue_item(
    output_dir: Path,
    attempt_id: str,
    score: int,
    eligible: bool,
    video_path: Path,
    metadata_path: Path,
    publish_mode: str,
    status: str,
    publisher: dict | None = None,
    content: dict | None = None,
) -> Path:
    path = queue_item_path(output_dir, attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempt_id": attempt_id,
        "created_at": datetime.now(UTC).isoformat(),
        "score": score,
        "eligible": eligible,
        "status": status,
        "publish_mode": publish_mode,
        "video_path": str(video_path.resolve()),
        "metadata_path": str(metadata_path.resolve()),
        "publisher": publisher,
    }
    if content:
        payload.update(content)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{attempt_id}-", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def queue_count(output_dir: Path) -> int:
    directory = output_dir / "queue"
    return sum(1 for item in directory.glob("*.json") if item.is_file()) if directory.is_dir() else 0


def read_queue(output_dir: Path) -> list[tuple[Path, dict]]:
    directory = output_dir / "queue"
    items: list[tuple[Path, dict]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Ungueltiger Queue-Eintrag: {path}") from exc
        if payload.get("attempt_id") != path.stem:
            raise ValueError(f"Queue-attempt_id stimmt nicht mit Dateiname ueberein: {path}")
        items.append((path, payload))
    return items


def update_queue_item(path: Path, updates: dict) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload

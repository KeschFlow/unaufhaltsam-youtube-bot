"""YouTube analytics collector; dry-run uses local data and makes no API calls."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .database import Database
from .metadata import HUMAN_RECORD, WALL
from .performance import hook_type, rate_performance, title_variant_from_title
from .runtime import read_runtime


def _local_rows(database: Database):
    with database.connection() as db:
        return db.execute(
            "SELECT v.attempt_id,v.video_path,v.metadata_path,r.reached_boxes,r.duration_seconds," 
            "r.completed,r.timestamp,"
            "(SELECT youtube_video_id FROM uploads u WHERE u.attempt_id=v.attempt_id "
            " AND u.status='published' ORDER BY u.id DESC LIMIT 1) video_id,"
            "(SELECT uploaded_at FROM uploads u WHERE u.attempt_id=v.attempt_id "
            " AND u.status='published' ORDER BY u.id DESC LIMIT 1) published_at "
            "FROM videos v JOIN runs r ON r.attempt_id=v.attempt_id ORDER BY r.timestamp"
        ).fetchall()


def _is_new_best(database: Database, attempt_id: str, score: int, timestamp: str) -> bool:
    with database.connection() as db:
        row = db.execute(
            "SELECT MAX(reached_boxes) best FROM runs WHERE timestamp < ? AND attempt_id <> ?",
            (timestamp, attempt_id),
        ).fetchone()
    return row["best"] is None or score > int(row["best"])


def _upsert_pending(database: Database, row, metrics: dict | None = None) -> dict:
    metrics = metrics or {}
    metadata = json.loads(Path(row["metadata_path"]).read_text(encoding="utf-8"))
    score = int(row["reached_boxes"])
    hook = hook_type(score, bool(row["completed"]))
    title_variant = title_variant_from_title(metadata["title"])
    new_best = _is_new_best(database, row["attempt_id"], score, row["timestamp"])
    features = {
        "hook_type": hook, "score": score, "duration_seconds": row["duration_seconds"],
        "new_best": new_best, **metrics,
    }
    rating, explanation = rate_performance(features)
    now = datetime.now(UTC).isoformat()
    values = {
        "video_id": row["video_id"], "published_at": row["published_at"],
        "score": score, "human_record": HUMAN_RECORD, "wall": WALL,
        "hook_type": hook, "title_variant": title_variant,
        "overlay_variant": "score_first", "performance_rating": rating,
        "rating_explanation": explanation,
        "metrics_status": "available" if metrics else "pending",
        "updated_at": now, **metrics,
    }
    columns = list(values)
    with database.connection() as db:
        existing = db.execute(
            "SELECT id FROM performance WHERE attempt_id=? ORDER BY id DESC LIMIT 1",
            (row["attempt_id"],),
        ).fetchone()
        if existing:
            assignments = ",".join(f"{column}=?" for column in columns)
            db.execute(
                f"UPDATE performance SET {assignments} WHERE id=?",
                (*[values[column] for column in columns], existing["id"]),
            )
        else:
            db.execute(
                f"INSERT INTO performance(attempt_id,captured_at,{','.join(columns)}) "
                f"VALUES(?,?,{','.join('?' for _ in columns)})",
                (row["attempt_id"], now, *[values[column] for column in columns]),
            )
    return {"attempt_id": row["attempt_id"], "metrics_status": values["metrics_status"], "rating": rating}


def run_analytics(config: Config, database: Database, mode: str | None = None) -> dict:
    mode = (mode or os.environ.get("ANALYTICS_MODE", read_runtime()["analytics_mode"])).lower()
    if mode not in {"dry_run", "api"}:
        raise ValueError("ANALYTICS_MODE muss dry_run oder api sein")
    if mode == "api":
        raise RuntimeError(
            "Analytics-API ist deaktiviert: Es werden keine zusaetzlichen OAuth-Scopes "
            "angefordert. ANALYTICS_MODE muss dry_run bleiben."
        )
    results = []
    for row in _local_rows(database):
        results.append(_upsert_pending(database, row, None))
    database.event("INFO", "analytics_updated", f"Analytics {mode} abgeschlossen", details={"count": len(results)})
    return {"mode": mode, "api_calls": mode == "api", "videos": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry_run", "api"), default=os.environ.get("ANALYTICS_MODE", "dry_run"))
    args = parser.parse_args(argv)
    config = Config.from_env()
    print(json.dumps(run_analytics(config, Database(config.db_file), args.mode), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

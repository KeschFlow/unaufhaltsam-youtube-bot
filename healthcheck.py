"""Operational health report for the local UNAUFHALTSAM autopilot."""

from __future__ import annotations

import json
from pathlib import Path

from unaufhaltsam_youtube.config import Config
from unaufhaltsam_youtube.database import Database
from unaufhaltsam_youtube.metadata import validate_metadata
from unaufhaltsam_youtube.preflight import run_preflight
from unaufhaltsam_youtube.queue import read_queue
from unaufhaltsam_youtube.runtime import read_runtime


def main() -> int:
    config = Config.from_env()
    database_ok = True
    last_run = None
    last_score = None
    last_error = None
    bot_ok = False
    renderer_ok = False
    try:
        database = Database(config.db_file)
        with database.connection() as db:
            run = db.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
            error = db.execute(
                "SELECT timestamp,event_type,message FROM system_events WHERE level IN "
                "('ERROR','CRITICAL') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            video = db.execute(
                "SELECT video_path,metadata_path FROM videos ORDER BY id DESC"
            ).fetchall()
        if run:
            last_run = run["timestamp"]
            last_score = run["reached_boxes"]
            bot_ok = bool(run["leaderboard_untouched"])
        if error:
            last_error = dict(error)
        for candidate in video:
            if Path(candidate["video_path"]).is_file() and Path(candidate["metadata_path"]).is_file():
                renderer_ok = True
                break
    except Exception as exc:
        database_ok = False
        last_error = {"event_type": "healthcheck", "message": str(exc)}

    queue_ok = True
    queued_videos = 0
    try:
        queue = read_queue(config.output_dir)
        queued_videos = len(queue)
        if queued_videos > config.max_queue_size:
            queue_ok = False
        for _, item in queue:
            metadata = json.loads(Path(item["metadata_path"]).read_text(encoding="utf-8"))
            validate_metadata(metadata)
    except Exception as exc:
        queue_ok = False
        last_error = {"event_type": "queue_health", "message": str(exc)}

    preflight = run_preflight(config)
    checks = preflight["checks"]
    report = {
        "scheduler_ok": checks["scheduler_present"],
        "bot_ok": bot_ok,
        "renderer_ok": renderer_ok,
        "queue_ok": queue_ok,
        "database_ok": database_ok,
        "publisher_mode": read_runtime()["publish_mode"],
        "oauth_ready": (
            checks["oauth_client_secret_present"] and checks["oauth_token_present"]
            and checks["upload_authorization_ready"]
        ),
        "upload_authorization_ready": checks["upload_authorization_ready"],
        "last_run": last_run,
        "last_score": last_score,
        "queued_videos": queued_videos,
        "last_error": last_error,
        "ready_for_public": preflight["READY_FOR_PUBLIC"],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

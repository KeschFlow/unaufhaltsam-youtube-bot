"""Daily analytics -> ranking -> one dry-run publication decision."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .analytics_worker import run_analytics
from .config import Config
from .content_policy import apply_policy_to_metadata, decide_content, validate_policy_output
from .database import Database
from .metadata import title_for, write_metadata
from .models import RunResult
from .publisher import publish
from .queue import update_queue_item
from .strategy_worker import select_best


def _load_run(database: Database, attempt_id: str) -> RunResult:
    with database.connection() as db:
        row = db.execute("SELECT * FROM runs WHERE attempt_id=?", (attempt_id,)).fetchone()
    if not row:
        raise ValueError(f"Ausgewaehlter Run fehlt: {attempt_id}")
    return RunResult(
        attempt_id=row["attempt_id"], timestamp=row["timestamp"],
        reached_boxes=row["reached_boxes"], rounds_completed=row["rounds_completed"],
        duration_seconds=row["duration_seconds"], failed=bool(row["failed"]),
        wall_reached=bool(row["wall_reached"]), completed=bool(row["completed"]),
        recording_path=row["recording_path"], strategy=row["strategy"],
        leaderboard_untouched=bool(row["leaderboard_untouched"]),
    )


def run_daily_performance_loop(config: Config, database: Database) -> dict:
    analytics = run_analytics(config, database)
    selected = select_best(config, database)
    if selected is None:
        return {"analytics": analytics, "selected": None, "publisher": None}
    if (
        config.publish_mode == "public"
        and database.count_public_uploads_today() >= config.max_uploads_per_day
    ):
        database.event(
            "INFO", "daily_upload_limit", "Upload-Tageslimit erreicht", selected.attempt_id
        )
        update_queue_item(Path(selected.queue_path), {"status": "daily_limit"})
        return {"analytics": analytics, "selected": asdict(selected), "publisher": None}
    run = _load_run(database, selected.attempt_id)
    content_decision = decide_content(database, run)
    metadata_path = Path(selected.metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["title"] = title_for(run, selected.title_variant)
    metadata = apply_policy_to_metadata(metadata, run, content_decision)
    validate_policy_output(metadata, run, content_decision)
    write_metadata(metadata, metadata_path)
    response = publish(metadata, config.publish_mode, database)
    status = "dry_run_marked" if config.publish_mode == "dry_run" else "published"
    update_queue_item(Path(selected.queue_path), {
        "status": status,
        "selected_for_next_upload": True,
        "publisher": response,
        "content_format": content_decision.content_format,
        "content_event": content_decision.event,
        "content_hook": content_decision.hook,
        "visualization": content_decision.visualization,
        "content_policy_passed": True,
    })
    with database.connection() as db:
        db.execute(
            "UPDATE strategy_selections SET status=? WHERE id=(SELECT id FROM "
            "strategy_selections WHERE attempt_id=? ORDER BY id DESC LIMIT 1)",
            (status, selected.attempt_id),
        )
    database.event(
        "INFO", "daily_strategy_selected", "Bestes Queue-Video markiert",
        selected.attempt_id, asdict(selected),
    )
    return {"analytics": analytics, "selected": asdict(selected), "publisher": response}

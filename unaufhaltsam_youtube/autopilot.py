"""Bounded autonomous UNAUFHALTSAM pipeline worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from .analytics_worker import run_analytics
from .bot_runner import BrowserGameError, run_bot
from .config import Config
from .content_policy import decide_content, validate_policy_output
from .database import Database
from .metadata import build_metadata, write_metadata
from .instance_lock import AutopilotAlreadyRunning, autopilot_lock
from .performance import hook_type
from .publisher import PublishError, publish
from .queue import update_queue_item, write_queue_item
from .renderer import RenderError, render_short
from .models import BOT_HARD_CAP, BOT_MIN_PUBLISH_SCORE
from .strategy_worker import recommend_variants


def _attempt_id() -> str:
    return f"attempt-{uuid.uuid4().hex[:12]}"


def execute_once(config: Config, database: Database) -> dict:
    attempt_id = _attempt_id()
    attempt_number = database.next_attempt_number()
    run = None
    last_error = None
    for retry in range(config.browser_retries + 1):
        try:
            run = run_bot(config, attempt_id)
            break
        except BrowserGameError as exc:
            last_error = exc
            database.event("ERROR", "browser_retry", str(exc), attempt_id, {"retry": retry})
    if run is None:
        database.event("CRITICAL", "browser_game_failure", str(last_error), attempt_id)
        raise BrowserGameError(str(last_error))

    database.add_run(run)
    database.event("INFO", "run_completed", "Echter Bot-Run gespeichert", attempt_id, run.as_dict())
    if not run.leaderboard_untouched:
        database.event("CRITICAL", "leaderboard_guardrail", "Leaderboard-Schreibversuch", attempt_id)
        raise BrowserGameError("Leaderboard-Guardrail ausgeloest")

    summary = {"run": run.as_dict(), "published": False}
    if run.reached_boxes > BOT_HARD_CAP:
        raise ValueError(f"HARD CAP VIOLATION: {run.reached_boxes} > {BOT_HARD_CAP}")

    eligible = (
        run.is_publishable
        and run.reached_boxes >= max(config.minimum_publish_score, BOT_MIN_PUBLISH_SCORE)
    )
    summary["eligible"] = eligible
    if not eligible:
        database.event(
            "INFO", "below_publish_window",
            f"Run bleibt unter Publish-Fenster {BOT_MIN_PUBLISH_SCORE}-{BOT_HARD_CAP}",
            attempt_id, run.as_dict(),
        )
        return summary

    if run.completed and not run.cap_reached:
        database.event("CRITICAL", "system_alert", "LEVEL 30 BROKEN", attempt_id, run.as_dict())

    selected_hook = hook_type(run.reached_boxes, run.completed and not run.cap_reached)
    title_variant, overlay_variant = recommend_variants(database, selected_hook)
    content_decision = decide_content(database, run)
    video_path = config.output_dir / "videos" / f"{attempt_id}.mp4"
    render_short(
        run, attempt_number, video_path, overlay_variant, content_decision.content_format
    )
    metadata = build_metadata(
        run, attempt_number, video_path, title_variant, content_decision
    )
    validate_policy_output(metadata, run, content_decision)
    metadata_path = config.output_dir / "metadata" / f"{attempt_id}.json"
    write_metadata(metadata, metadata_path)
    database.add_video(attempt_id, video_path, metadata_path)
    queue_path = write_queue_item(
        config.output_dir, attempt_id, run.reached_boxes, eligible, video_path, metadata_path,
        config.publish_mode, "candidate", content={
            "content_format": content_decision.content_format,
            "content_event": content_decision.event,
            "content_hook": content_decision.hook,
            "visualization": content_decision.visualization,
            "content_policy_passed": True,
            "new_bot_best": content_decision.new_bot_best,
        },
    )
    summary.update({
        "video_path": str(video_path),
        "metadata_path": str(metadata_path),
        "queue_path": str(queue_path),
        "eligible": eligible,
        "content_event": content_decision.event,
        "new_bot_best": content_decision.new_bot_best,
    })
    return summary


def _candidate_priority(summary: dict) -> tuple[int, int]:
    """Prefer wall/record runs; otherwise use the best genuine run of the day."""
    run = summary["run"]
    event = summary.get("content_event")
    if run.get("completed") or run.get("wall_reached"):
        tier = 4
    elif summary.get("new_bot_best"):
        tier = 3
    elif event == "repeated_failure_score":
        tier = 2
    else:
        tier = 1
    return tier, int(run.get("reached_boxes", 0))


def _publish_daily_candidate(config: Config, database: Database, results: list[dict]) -> None:
    """Publish at most one real Short: the strongest truthful candidate generated today."""
    candidates = [item for item in results if item.get("eligible") and item.get("metadata_path")]
    if not candidates:
        return
    if database.count_public_uploads_today() >= config.max_uploads_per_day:
        for item in candidates:
            update_queue_item(Path(item["queue_path"]), {"status": "daily_limit"})
        return

    selected = max(candidates, key=_candidate_priority)
    for item in candidates:
        if item is not selected:
            update_queue_item(Path(item["queue_path"]), {"status": "held_for_fail_pool"})

    metadata = json.loads(Path(selected["metadata_path"]).read_text(encoding="utf-8"))
    response = publish(metadata, config.publish_mode, database)
    update_queue_item(Path(selected["queue_path"]), {
        "status": "dry_run_validated" if config.publish_mode == "dry_run" else "published",
        "publisher": response,
        "daily_selection": "wall_or_record_first_then_best_real_run",
    })
    selected.update({
        "published": config.publish_mode == "public",
        "publisher_validated": True,
        "publisher": response,
    })


def run_worker(config: Config, once: bool = False) -> list[dict]:
    database = Database(config.db_file)
    # Refresh performance data, but do not consume today's upload slot before
    # today's runs have been generated and compared with each other.
    run_analytics(config, database)
    results: list[dict] = []
    remaining = max(0, config.max_runs_per_day - database.count_today("runs"))
    if once:
        remaining = min(1, remaining)
    for index in range(remaining):
        try:
            summary = execute_once(config, database)
            results.append(summary)
            if summary["run"].get("cap_reached") or summary["run"]["completed"]:
                break
        except (BrowserGameError, RenderError, PublishError, ValueError) as exc:
            event_type = (
                "render_failure" if isinstance(exc, RenderError) else
                "oauth_api_failure" if isinstance(exc, PublishError) else
                "metadata_or_browser_failure"
            )
            database.event("CRITICAL", event_type, str(exc))
            raise
        if index + 1 < remaining and config.cooldown_minutes:
            time.sleep(config.cooldown_minutes * 60)

    _publish_daily_candidate(config, database, results)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Genau einen echten Run ausfuehren")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.from_env()
        with autopilot_lock(config.output_dir):
            results = run_worker(config, once=args.once)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    except AutopilotAlreadyRunning as exc:
        print(f"Autopilot nicht gestartet: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"Autopilot gestoppt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

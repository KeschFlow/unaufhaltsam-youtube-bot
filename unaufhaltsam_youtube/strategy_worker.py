"""Deterministic performance-aware queue ranking and strategy selection."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .content_policy import decide_content
from .database import Database
from .models import RunResult
from .performance import hook_type, queue_priority
from .queue import read_queue, update_queue_item


@dataclass(frozen=True)
class RankedCandidate:
    attempt_id: str
    score: int
    priority_score: float
    priority_reason: str
    hook_type: str
    title_variant: str
    overlay_variant: str
    content_format: str
    content_event: str
    visualization: str
    queue_path: str
    metadata_path: str
    video_path: str


def recommend_variants(database: Database, hook: str) -> tuple[str, str]:
    with database.connection() as db:
        rows = db.execute(
            "SELECT title_variant,overlay_variant,AVG(performance_rating) rating,COUNT(*) samples "
            "FROM performance WHERE hook_type=? AND metrics_status='available' "
            "GROUP BY title_variant,overlay_variant ORDER BY rating DESC,samples DESC LIMIT 1",
            (hook,),
        ).fetchone()
    if rows and rows["samples"] >= 2:
        return rows["title_variant"] or "result_first", rows["overlay_variant"] or "score_first"
    defaults = {
        "LEVEL_30_BROKEN": ("result_first", "hook_first"),
        "WALL_REACHED": ("duel_first", "hook_first"),
        "BOT_BEATS_HUMAN_RECORD": ("duel_first", "record_first"),
        "BOT_TIES_HUMAN_RECORD": ("duel_first", "record_first"),
        "BOT_FAILED_HUMAN_LEADS": ("score_first", "score_first"),
    }
    return defaults[hook]


def rank_queue(config: Config, database: Database) -> list[RankedCandidate]:
    ranked: list[RankedCandidate] = []
    with database.connection() as db:
        published = {
            row["attempt_id"]
            for row in db.execute("SELECT attempt_id FROM uploads WHERE status='published'")
        }
        run_rows = {
            row["attempt_id"]: row
            for row in db.execute("SELECT * FROM runs ORDER BY timestamp")
        }
        ratings = {
            row["attempt_id"]: row["performance_rating"]
            for row in db.execute(
                "SELECT p.attempt_id,p.performance_rating FROM performance p "
                "JOIN (SELECT attempt_id,MAX(id) id FROM performance GROUP BY attempt_id) x "
                "ON x.id=p.id"
            )
        }
    ordered_runs = sorted(run_rows.values(), key=lambda row: row["timestamp"])
    previous_best: dict[str, int] = {}
    best = -1
    for row in ordered_runs:
        previous_best[row["attempt_id"]] = best
        best = max(best, int(row["reached_boxes"]))
    for path, payload in read_queue(config.output_dir):
        attempt_id = payload["attempt_id"]
        if attempt_id in published:
            update_queue_item(path, {"status": "published_excluded"})
            continue
        row = run_rows.get(attempt_id)
        if row is None:
            raise ValueError(f"Queue-Run fehlt in SQLite: {attempt_id}")
        score = int(row["reached_boxes"])
        eligible = 10 <= score <= 15 and score >= config.minimum_publish_score
        if payload.get("eligible") != eligible:
            update_queue_item(path, {"eligible": eligible})
        if config.publish_mode == "public" and not eligible:
            update_queue_item(path, {"status": "below_threshold", "eligible": False})
            continue
        new_best = score > previous_best[attempt_id]
        hook = hook_type(score, bool(row["completed"]))
        title_variant, overlay_variant = recommend_variants(database, hook)
        run = RunResult(
            attempt_id=row["attempt_id"], timestamp=row["timestamp"],
            reached_boxes=row["reached_boxes"], rounds_completed=row["rounds_completed"],
            duration_seconds=row["duration_seconds"], failed=bool(row["failed"]),
            wall_reached=bool(row["wall_reached"]), completed=bool(row["completed"]),
            recording_path=row["recording_path"], strategy=row["strategy"],
            leaderboard_untouched=bool(row["leaderboard_untouched"]),
            cap_reached=bool(row["cap_reached"]),
        )
        content = decide_content(database, run)
        priority_score, reason = queue_priority(
            score, bool(row["completed"]), new_best, ratings.get(attempt_id)
        )
        candidate = RankedCandidate(
            attempt_id, score, priority_score, reason, hook, title_variant, overlay_variant,
            content.content_format, content.event, content.visualization,
            str(path.resolve()), payload["metadata_path"], payload["video_path"],
        )
        ranked.append(candidate)
    ranked.sort(key=lambda item: (-item.priority_score, item.attempt_id))
    for position, candidate in enumerate(ranked, 1):
        queue_path = Path(candidate.queue_path)
        current = json.loads(queue_path.read_text(encoding="utf-8"))
        updates = {
            "rank": position,
            "priority_score": candidate.priority_score,
            "priority_reason": candidate.priority_reason,
            "hook_type": candidate.hook_type,
            "title_variant": candidate.title_variant,
            "overlay_variant": candidate.overlay_variant,
            "content_format": candidate.content_format,
            "content_event": candidate.content_event,
            "visualization": candidate.visualization,
            "content_policy_passed": True,
        }
        if current.get("status") not in {"selected", "dry_run_marked", "published"}:
            updates["status"] = "ranked"
        update_queue_item(queue_path, updates)
        with database.connection() as db:
            db.execute(
                "UPDATE performance SET hook_type=?,title_variant=?,overlay_variant=?,"
                "publish_score=?,updated_at=? WHERE id=(SELECT id FROM performance "
                "WHERE attempt_id=? ORDER BY id DESC LIMIT 1)",
                (candidate.hook_type, candidate.title_variant, candidate.overlay_variant,
                 candidate.priority_score, datetime.now(UTC).isoformat(), candidate.attempt_id),
            )
    while len(ranked) > config.max_queue_size:
        removable = None
        for candidate in reversed(ranked):
            payload = json.loads(Path(candidate.queue_path).read_text(encoding="utf-8"))
            if payload.get("status") not in {"selected", "dry_run_marked", "published"}:
                removable = candidate
                break
        if removable is None:
            break
        queue_path = Path(removable.queue_path).resolve()
        video_path = Path(removable.video_path).resolve()
        output_root = config.output_dir.resolve()
        if output_root not in queue_path.parents or output_root not in video_path.parents:
            raise ValueError("Queue-Bereinigung verweigert Pfad ausserhalb output")
        queue_path.unlink(missing_ok=True)
        video_path.unlink(missing_ok=True)
        ranked.remove(removable)
        with database.connection() as db:
            db.execute(
                "UPDATE performance SET metrics_status='queue_pruned',updated_at=? WHERE "
                "id=(SELECT id FROM performance WHERE attempt_id=? ORDER BY id DESC LIMIT 1)",
                (datetime.now(UTC).isoformat(), removable.attempt_id),
            )
        database.event(
            "INFO", "queue_pruned", "Niedrigst priorisiertes unveroeffentlichtes Short entfernt",
            removable.attempt_id,
            {"priority_score": removable.priority_score, "database_history_preserved": True},
        )
    return ranked


def select_best(config: Config, database: Database) -> RankedCandidate | None:
    ranked = rank_queue(config, database)
    if not ranked:
        return None
    with database.connection() as db:
        today = db.execute(
            "SELECT 1 FROM strategy_selections WHERE date(selected_at,'localtime')="
            "date('now','localtime') AND status IN ('selected','dry_run_marked','published') LIMIT 1"
        ).fetchone()
        if today:
            return None
        selected = ranked[0]
        db.execute(
            "INSERT INTO strategy_selections(selected_at,attempt_id,priority_score,priority_reason,"
            "hook_type,title_variant,overlay_variant,status) VALUES(?,?,?,?,?,?,?,'selected')",
            (datetime.now(UTC).isoformat(), selected.attempt_id, selected.priority_score,
             selected.priority_reason, selected.hook_type, selected.title_variant,
             selected.overlay_variant),
        )
    update_queue_item(Path(selected.queue_path), {"status": "selected"})
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select", action="store_true")
    args = parser.parse_args(argv)
    config = Config.from_env()
    database = Database(config.db_file)
    selected = select_best(config, database) if args.select else None
    ranked = rank_queue(config, database)
    print(json.dumps({"selected": asdict(selected) if selected else None,
                      "ranked": [asdict(item) for item in ranked]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

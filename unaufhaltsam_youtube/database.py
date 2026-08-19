"""SQLite persistence and duplicate-publication protection."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .models import RunResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  attempt_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, reached_boxes INTEGER NOT NULL,
  rounds_completed INTEGER NOT NULL, duration_seconds REAL NOT NULL, failed INTEGER NOT NULL,
  wall_reached INTEGER NOT NULL, completed INTEGER NOT NULL, recording_path TEXT NOT NULL,
  strategy TEXT NOT NULL, leaderboard_untouched INTEGER NOT NULL, cap_reached INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL UNIQUE REFERENCES runs(attempt_id),
  video_path TEXT NOT NULL UNIQUE, metadata_path TEXT NOT NULL, rendered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES runs(attempt_id),
  mode TEXT NOT NULL, status TEXT NOT NULL, youtube_video_id TEXT, uploaded_at TEXT NOT NULL,
  response_json TEXT, UNIQUE(attempt_id, mode)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_public_upload_per_attempt
  ON uploads(attempt_id) WHERE status = 'published';
CREATE TABLE IF NOT EXISTS performance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES runs(attempt_id),
  captured_at TEXT NOT NULL, views INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0, watch_time_minutes REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, level TEXT NOT NULL,
  event_type TEXT NOT NULL, attempt_id TEXT, message TEXT NOT NULL, details_json TEXT
);
CREATE TABLE IF NOT EXISTS strategy_selections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, selected_at TEXT NOT NULL,
  attempt_id TEXT NOT NULL REFERENCES runs(attempt_id), priority_score REAL NOT NULL,
  priority_reason TEXT NOT NULL, hook_type TEXT NOT NULL, title_variant TEXT NOT NULL,
  overlay_variant TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_state (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

PERFORMANCE_COLUMNS = {
    "video_id": "TEXT",
    "published_at": "TEXT",
    "views_24h": "INTEGER",
    "views_48h": "INTEGER",
    "likes_24h": "INTEGER",
    "likes_48h": "INTEGER",
    "comments_24h": "INTEGER",
    "comments_48h": "INTEGER",
    "average_view_duration": "REAL",
    "average_percentage_viewed": "REAL",
    "impressions": "INTEGER",
    "ctr": "REAL",
    "subscribers_gained": "INTEGER",
    "score": "INTEGER",
    "human_record": "INTEGER",
    "wall": "INTEGER",
    "hook_type": "TEXT",
    "title_variant": "TEXT",
    "overlay_variant": "TEXT",
    "publish_score": "REAL",
    "performance_rating": "REAL",
    "rating_explanation": "TEXT",
    "metrics_status": "TEXT NOT NULL DEFAULT 'pending'",
    "updated_at": "TEXT",
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            existing = {
                row["name"] for row in connection.execute("PRAGMA table_info(performance)")
            }
            for name, declaration in PERFORMANCE_COLUMNS.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE performance ADD COLUMN {name} {declaration}")
            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)")
            }
            if "cap_reached" not in run_columns:
                connection.execute("ALTER TABLE runs ADD COLUMN cap_reached INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def add_run(self, run: RunResult) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO runs(attempt_id,timestamp,reached_boxes,rounds_completed,duration_seconds,"
                "failed,wall_reached,completed,recording_path,strategy,leaderboard_untouched,cap_reached) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.attempt_id, run.timestamp, run.reached_boxes, run.rounds_completed,
                    run.duration_seconds, run.failed, run.wall_reached, run.completed,
                    run.recording_path, run.strategy, run.leaderboard_untouched, run.cap_reached,
                ),
            )

    def add_video(self, attempt_id: str, video_path: Path, metadata_path: Path) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO videos(attempt_id, video_path, metadata_path, rendered_at) VALUES(?,?,?,?)",
                (attempt_id, str(video_path), str(metadata_path), datetime.now(UTC).isoformat()),
            )

    def add_upload(
        self, attempt_id: str, mode: str, status: str, video_id: str | None, response: dict
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute(
                "INSERT INTO uploads(attempt_id,mode,status,youtube_video_id,uploaded_at,response_json) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(attempt_id,mode) DO UPDATE SET "
                "status=excluded.status,youtube_video_id=excluded.youtube_video_id,"
                "uploaded_at=excluded.uploaded_at,response_json=excluded.response_json",
                (attempt_id, mode, status, video_id, now, json.dumps(response)),
            )
            exists = db.execute(
                "SELECT 1 FROM performance WHERE attempt_id=? LIMIT 1", (attempt_id,)
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO performance(attempt_id,captured_at,metrics_status,updated_at) "
                    "VALUES(?,?,'pending',?)", (attempt_id, now, now)
                )

    def has_public_upload(self, attempt_id: str) -> bool:
        with self.connection() as db:
            row = db.execute(
                "SELECT 1 FROM uploads WHERE attempt_id=? AND status='published'", (attempt_id,)
            ).fetchone()
        return row is not None

    def reserve_public_upload(self, attempt_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            other = db.execute(
                "SELECT attempt_id FROM uploads WHERE mode='public' AND status='uploading' "
                "AND attempt_id<>? LIMIT 1", (attempt_id,)
            ).fetchone()
            if other:
                raise RuntimeError("Ein anderer Public-Upload laeuft bereits")
            existing = db.execute(
                "SELECT status FROM uploads WHERE attempt_id=? AND mode='public'",
                (attempt_id,),
            ).fetchone()
            if existing and existing["status"] in {"uploading", "published"}:
                raise RuntimeError(f"attempt_id {attempt_id} ist fuer Public bereits gesperrt")
            db.execute(
                "INSERT INTO uploads(attempt_id,mode,status,uploaded_at,response_json) "
                "VALUES(?,'public','uploading',?,'{}') ON CONFLICT(attempt_id,mode) DO UPDATE SET "
                "status='uploading',uploaded_at=excluded.uploaded_at,response_json='{}'",
                (attempt_id, now),
            )

    def finalize_public_upload(self, attempt_id: str, video_id: str, response: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            cursor = db.execute(
                "UPDATE uploads SET status='published',youtube_video_id=?,uploaded_at=?,"
                "response_json=? WHERE attempt_id=? AND mode='public' AND status='uploading'",
                (video_id, now, json.dumps(response), attempt_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Public-Upload-Reservierung fehlt")
            db.execute(
                "INSERT INTO publication_state(key,value,updated_at) VALUES"
                "('first_public_upload_completed','true',?) ON CONFLICT(key) DO UPDATE SET "
                "value='true',updated_at=excluded.updated_at",
                (now,),
            )
            exists = db.execute(
                "SELECT 1 FROM performance WHERE attempt_id=? LIMIT 1", (attempt_id,)
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO performance(attempt_id,captured_at,video_id,published_at,"
                    "metrics_status,updated_at) VALUES(?,?,?,?, 'pending',?)",
                    (attempt_id, now, video_id, now, now),
                )

    def fail_public_upload(self, attempt_id: str, message: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute(
                "UPDATE uploads SET status='failed',uploaded_at=?,response_json=? "
                "WHERE attempt_id=? AND mode='public'",
                (now, json.dumps({"error": message}), attempt_id),
            )

    def count_today(self, table: str) -> int:
        if table not in {"runs", "uploads"}:
            raise ValueError("Ungueltige Tabelle")
        column = "timestamp" if table == "runs" else "uploaded_at"
        with self.connection() as db:
            row = db.execute(
                f"SELECT COUNT(*) n FROM {table} WHERE date({column})=date('now')"
            ).fetchone()
        return int(row["n"])

    def count_public_uploads_today(self) -> int:
        """Count real public upload attempts; dry-run validations are not uploads."""
        with self.connection() as db:
            row = db.execute(
                "SELECT COUNT(*) n FROM uploads WHERE mode='public' "
                "AND date(uploaded_at)=date('now')"
            ).fetchone()
        return int(row["n"])

    def next_attempt_number(self) -> int:
        with self.connection() as db:
            row = db.execute("SELECT COUNT(*) n FROM runs").fetchone()
        return int(row["n"]) + 1

    def event(
        self, level: str, event_type: str, message: str,
        attempt_id: str | None = None, details: dict | None = None,
    ) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO system_events(timestamp,level,event_type,attempt_id,message,details_json) "
                "VALUES(?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(), level, event_type, attempt_id, message,
                 json.dumps(details) if details else None),
            )

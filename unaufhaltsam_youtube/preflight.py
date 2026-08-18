"""Read-only public readiness checks. Never uploads or changes OAuth configuration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from .auth import (
    SCOPES,
    YOUTUBE_UPLOAD_SCOPE,
    validate_client_secret,
    validate_credentials_scope,
)
from .config import Config
from .database import Database
from .instance_lock import is_autopilot_running
from .metadata import validate_metadata
from .paths import client_secret_file, token_file
from .queue import read_queue
from .runtime import read_runtime

TASK_NAME = "UNAUFHALTSAM YouTube Autopilot"


def scheduler_present() -> bool:
    process = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    normalized = process.stdout.lower()
    return process.returncode == 0 and "<enabled>false</enabled>" not in normalized


def _queue_checks(config: Config, database: Database) -> tuple[bool, bool, int, list[str]]:
    errors: list[str] = []
    items = read_queue(config.output_dir)
    attempt_ids = [payload.get("attempt_id") for _, payload in items]
    no_duplicates = len(attempt_ids) == len(set(attempt_ids))
    if not no_duplicates:
        errors.append("Queue enthaelt doppelte attempt_ids")
    valid_count = 0
    for _, payload in items:
        try:
            metadata = json.loads(Path(payload["metadata_path"]).read_text(encoding="utf-8"))
            validate_metadata(metadata)
            if metadata["attempt_id"] != payload["attempt_id"]:
                raise ValueError("attempt_id in Metadata weicht ab")
            if not payload.get("content_policy_passed", False):
                raise ValueError("Content-Policy-Guard fehlt")
            valid_count += 1
        except Exception as exc:
            errors.append(f"{payload.get('attempt_id')}: {exc}")
    with database.connection() as db:
        duplicate_videos = db.execute(
            "SELECT attempt_id FROM videos GROUP BY attempt_id HAVING COUNT(*)>1"
        ).fetchall()
        duplicate_public = db.execute(
            "SELECT attempt_id FROM uploads WHERE status='published' GROUP BY attempt_id "
            "HAVING COUNT(*)>1"
        ).fetchall()
    if duplicate_videos or duplicate_public:
        no_duplicates = False
        errors.append("SQLite enthaelt doppelte Video-/Public-attempt_ids")
    return valid_count > 0, no_duplicates, len(items), errors


def run_preflight(config: Config | None = None, check_channel: bool = True) -> dict[str, Any]:
    config = config or Config.from_env()
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    secret = client_secret_file()
    try:
        validate_client_secret(secret)
        checks["oauth_client_secret_present"] = True
    except Exception as exc:
        checks["oauth_client_secret_present"] = False
        details["oauth_client_secret_error"] = str(exc)

    credentials = None
    token = token_file()
    checks["oauth_token_present"] = token.is_file()
    checks["youtube_upload_scope_present"] = False
    checks["token_refreshable"] = False
    checks["oauth_token_valid"] = False
    if checks["oauth_token_present"]:
        try:
            credentials = Credentials.from_authorized_user_file(token, SCOPES)
            validate_credentials_scope(credentials)
            scopes = set(credentials.scopes or ())
            checks["youtube_upload_scope_present"] = scopes == {YOUTUBE_UPLOAD_SCOPE}
            checks["token_refreshable"] = bool(credentials.refresh_token)
            if checks["token_refreshable"]:
                credentials.refresh(Request())
                validate_credentials_scope(credentials)
            checks["oauth_token_valid"] = bool(credentials.valid)
        except Exception as exc:
            details["oauth_token_error"] = str(exc)

    # youtube.upload authorizes uploads but not a read-only channels.list probe.
    # A successful refresh plus the exact granted scope is the strongest safe
    # validation available without uploading a video.
    checks["upload_authorization_ready"] = (
        checks["youtube_upload_scope_present"]
        and checks["token_refreshable"]
        and checks["oauth_token_valid"]
    )

    try:
        database = Database(config.db_file)
        with database.connection() as db:
            checks["sqlite_reachable"] = db.execute("SELECT 1").fetchone()[0] == 1
        queue_valid, no_duplicates, queue_count, queue_errors = _queue_checks(config, database)
        checks["queue_has_valid_video"] = queue_valid
        checks["no_duplicate_attempt_id"] = no_duplicates
        details["queue_count"] = queue_count
        if queue_errors:
            details["queue_errors"] = queue_errors
    except Exception as exc:
        checks["sqlite_reachable"] = False
        checks["queue_has_valid_video"] = False
        checks["no_duplicate_attempt_id"] = False
        details["database_or_queue_error"] = str(exc)

    checks["scheduler_present"] = scheduler_present()
    checks["no_parallel_autopilot"] = not is_autopilot_running(config.output_dir)
    checks["publish_mode_is_dry_run"] = read_runtime()["publish_mode"] == "dry_run"
    checks["publisher_configured"] = (
        checks["sqlite_reachable"] and checks["queue_has_valid_video"]
        and checks["youtube_upload_scope_present"]
    )
    ready = all(checks.values())
    return {"checks": checks, "details": details, "READY_FOR_PUBLIC": ready}

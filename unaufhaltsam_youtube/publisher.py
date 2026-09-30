"""YouTube upload publisher with full dry-run validation."""

from __future__ import annotations

from pathlib import Path
import hashlib

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .auth import authorize
from .database import Database
from .metadata import validate_metadata
from .runtime import set_publish_mode


class PublishError(RuntimeError):
    pass


def upload_video_with_credentials(metadata: dict, credentials) -> dict:
    """Perform the real YouTube upload with caller-provided OAuth credentials."""
    title = metadata.get("title")
    description = metadata.get("description")
    tags = metadata.get("tags")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise PublishError("Uploadtitel fehlt oder ist ungueltig")
    if not isinstance(description, str) or len(description) > 5000:
        raise PublishError("Uploadbeschreibung ist ungueltig")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise PublishError("Uploadtags sind ungueltig")
    video_path = Path(metadata["video_path"])
    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise PublishError("Uploadvideo fehlt oder ist leer")
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": "24",
            },
            "status": {
                "privacyStatus": metadata.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    if not response.get("id"):
        raise PublishError("YouTube-Antwort enthaelt keine Video-ID")
    return response


def publish(metadata: dict, mode: str, database: Database) -> dict:
    attempt_id = metadata.get("attempt_id", "unknown")
    try:
        validate_metadata(metadata)
        if mode == "public":
            fairness = metadata.get("fairness") or {}
            if fairness.get("ruleset") != "AI-vs-THE-WALL-V1" or fairness.get("strategy") != "visual_v1":
                raise PublishError("FAIRNESS_FAIL: nur visuell belegte V1-Runs duerfen hochgeladen werden")
            for file_key, hash_key in (("recording_path", "recording_sha256"), ("event_log_path", "event_log_sha256")):
                file = Path(fairness.get(file_key) or "")
                if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != fairness.get(hash_key):
                    raise PublishError("FAIRNESS_FAIL: Videobeweis oder Eventlog-Hash ungueltig")
        if database.has_public_upload(attempt_id):
            raise PublishError(f"attempt_id {attempt_id} wurde bereits veroeffentlicht")
        video_path = Path(metadata["video_path"])
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise PublishError("Uploadvideo fehlt oder ist leer")
    except Exception as exc:
        if mode == "public":
            message = f"Public-Vorpruefung fehlgeschlagen: {exc}"
            database.event("CRITICAL", "public_upload_failure", message, attempt_id)
            set_publish_mode("dry_run")
        raise PublishError(str(exc)) from exc
    checks = {
        "metadata_valid": True,
        "video_exists": video_path.is_file(),
        "video_nonempty": video_path.is_file() and video_path.stat().st_size > 0,
        "duplicate_publication": False,
        "scope": "https://www.googleapis.com/auth/youtube.upload",
    }
    if mode == "dry_run":
        response = {"mode": mode, "status": "validated_no_upload", "checks": checks}
        database.add_upload(attempt_id, mode, "dry_run_ok", None, response)
        return response
    if mode != "public":
        raise PublishError("Unbekannter Publish-Modus")
    try:
        database.reserve_public_upload(attempt_id)
        upload_metadata = dict(metadata)
        upload_metadata["privacy_status"] = "public"
        response = upload_video_with_credentials(upload_metadata, authorize())
        video_id = response["id"]
        database.finalize_public_upload(attempt_id, video_id, response)
    except Exception as exc:
        message = f"YouTube API/OAuth fehlgeschlagen: {exc}"
        database.fail_public_upload(attempt_id, message)
        database.event("CRITICAL", "public_upload_failure", message, attempt_id)
        set_publish_mode("dry_run")
        raise PublishError(message) from exc
    return {"mode": mode, "status": "published", "video_id": video_id}

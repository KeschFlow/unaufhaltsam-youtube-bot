"""Transparent YouTube metadata for capped bot demonstration runs."""

from __future__ import annotations

import json
from pathlib import Path

from .models import BOT_HARD_CAP, BOT_MIN_PUBLISH_SCORE, RunResult

HUMAN_RECORD = 26
WALL = 30


def result_hook(score: int, completed: bool = False, cap_reached: bool = False) -> str:
    if cap_reached:
        return f"BOT RUN CAP REACHED AT {BOT_HARD_CAP}."
    if completed:
        return "LEVEL 30 BROKEN."
    if score < HUMAN_RECORD:
        return "BOT RUN ENDED. HUMAN RECORD REMAINS SEPARATE."
    if score == HUMAN_RECORD:
        return "BOT RUN MATCHED THE HUMAN RECORD."
    if score < WALL:
        return "BOT RUN PASSED THE HUMAN RECORD. WALL STILL STANDS."
    return "THE BOT REACHED THE WALL."


def title_for(run: RunResult, variant: str = "result_first") -> str:
    score = run.reached_boxes
    if run.cap_reached:
        return f"UNAUFHALTSAM | Bot Demonstration Run ({score}/{BOT_HARD_CAP})"
    if variant == "score_first":
        return f"UNAUFHALTSAM | Bot Demonstration Run ({score}/{BOT_HARD_CAP})"
    if variant == "duel_first":
        return f"UNAUFHALTSAM Bot Demo: {score}/{BOT_HARD_CAP}"
    if variant != "result_first":
        raise ValueError(f"Unbekannte Titelvariante: {variant}")
    return f"UNAUFHALTSAM | Bot Demonstration Run ({score}/{BOT_HARD_CAP})"


def build_metadata(
    run: RunResult, attempt_number: int, video_path: Path,
    title_variant: str = "result_first", content_decision=None,
) -> dict:
    hook = result_hook(run.reached_boxes, run.completed, run.cap_reached)
    cap_status = "CAP REACHED" if run.cap_reached else "CAP NOT REACHED"
    description = (
        f"BOT RUN #{attempt_number}\n"
        f"BOT SCORE: {run.reached_boxes}\n"
        f"BOT CAP: {BOT_HARD_CAP}\n"
        f"STATUS: {cap_status}\n\n{hook}\n\n"
        "Automated system demonstration run. Bot execution is capped at 15. "
        f"Human record ({HUMAN_RECORD}) and Wall ({WALL}) are separate human-game metrics.\n\n"
        "PLAY UNAUFHALTSAM\nhttps://eddiesworld.de/unaufhaltsam/"
    )
    metadata = {
        "title": title_for(run, title_variant),
        "description": description,
        "tags": ["UNAUFHALTSAM", "BotRun", "FocusGame", "AutomationTest"],
        "attempt_id": run.attempt_id,
        "score": run.reached_boxes,
        "human_record": HUMAN_RECORD,
        "wall": WALL,
        "video_path": str(video_path.resolve()),
    }
    if run.fairness_passed:
        metadata["fairness"] = {
            "ruleset": run.bot_version, "strategy": run.strategy,
            "recording_path": run.recording_path, "recording_sha256": run.recording_sha256,
            "event_log_path": run.evidence_path, "event_log_sha256": run.evidence_sha256,
            "game_url": run.game_url,
            "browser_version": run.browser_version, "runtime_version": run.runtime_version,
            "runner_sha256": run.runner_sha256, "vision_sha256": run.vision_sha256,
        }
    if content_decision is not None:
        from .content_policy import apply_policy_to_metadata
        metadata = apply_policy_to_metadata(metadata, run, content_decision)
    return metadata


def validate_metadata(metadata: dict) -> None:
    required = {"title", "description", "tags", "attempt_id", "score", "human_record", "wall", "video_path"}
    if not required.issubset(metadata) or set(metadata) - required - {"fairness"}:
        raise ValueError("Metadatenfelder sind ungueltig")
    if not metadata["title"] or len(metadata["title"]) > 100:
        raise ValueError("YouTube-Titel fehlt oder ist zu lang")
    if not isinstance(metadata["tags"], list) or not metadata["attempt_id"]:
        raise ValueError("Tags oder attempt_id sind ungueltig")
    if metadata["human_record"] != HUMAN_RECORD or metadata["wall"] != WALL:
        raise ValueError("Rekord- oder Wall-Wert ist ungueltig")
    if not BOT_MIN_PUBLISH_SCORE <= int(metadata["score"]) <= BOT_HARD_CAP:
        raise ValueError("Bot-Score liegt ausserhalb des Publish-Fensters 10-15")
    if f"BOT SCORE: {metadata['score']}" not in metadata["description"]:
        raise ValueError("Beschreibung widerspricht dem Score")
    if not Path(metadata["video_path"]).is_file():
        raise ValueError("Video aus Metadaten fehlt")


def write_metadata(metadata: dict, path: Path) -> Path:
    validate_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

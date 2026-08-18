"""Truthful result-driven YouTube metadata."""

from __future__ import annotations

import json
from pathlib import Path

from .models import RunResult

HUMAN_RECORD = 26
WALL = 30


def result_hook(score: int, completed: bool = False) -> str:
    if completed:
        return "LEVEL 30 BROKEN."
    if score < HUMAN_RECORD:
        return "BOT FAILED. HUMAN STILL LEADS."
    if score == HUMAN_RECORD:
        return "BOT TIES THE HUMAN RECORD."
    if score < WALL:
        return "BOT BEATS THE HUMAN RECORD. THE WALL IS STILL STANDING."
    return "THE BOT REACHED THE WALL."


def title_for(run: RunResult, variant: str = "result_first") -> str:
    score = run.reached_boxes
    if variant == "score_first":
        if run.completed:
            return f"{score} Boxen: LEVEL 30 BROKEN"
        return f"{score} Boxen: echter Bot-vs.-Mensch-Versuch"
    if variant == "duel_first":
        if run.completed:
            return "BOT vs. MENSCH: Level 30 tatsächlich gebrochen"
        return f"BOT vs. MENSCH: Der Bot erreicht {score} Boxen"
    if variant != "result_first":
        raise ValueError(f"Unbekannte Titelvariante: {variant}")
    if run.completed:
        return "LEVEL 30 BROKEN – echter UNAUFHALTSAM Bot-Run"
    if score >= WALL:
        return f"Der Bot erreicht die Wall: {score} Boxen"
    if score > HUMAN_RECORD:
        return f"Bot schlägt den Menschen mit {score} Boxen"
    if score == HUMAN_RECORD:
        return "Bot gleicht den menschlichen Rekord von 26 Boxen aus"
    return f"Bot Attempt endet bei {score} Boxen – Mensch führt"


def build_metadata(
    run: RunResult, attempt_number: int, video_path: Path,
    title_variant: str = "result_first", content_decision=None,
) -> dict:
    hook = result_hook(run.reached_boxes, run.completed)
    description = (
        f"BOT ATTEMPT #{attempt_number}\n"
        f"BOT SCORE: {run.reached_boxes}\n"
        f"HUMAN RECORD: {HUMAN_RECORD}\n"
        f"WALL: {WALL}\n\n{hook}\n\n"
        "PLAY UNAUFHALTSAM\nhttps://eddiesworld.de/unaufhaltsam/"
    )
    metadata = {
        "title": title_for(run, title_variant),
        "description": description,
        "tags": ["UNAUFHALTSAM", "BotVsMensch", "YouTubeShorts", "FocusGame"],
        "attempt_id": run.attempt_id,
        "score": run.reached_boxes,
        "human_record": HUMAN_RECORD,
        "wall": WALL,
        "video_path": str(video_path.resolve()),
    }
    if content_decision is not None:
        from .content_policy import apply_policy_to_metadata
        metadata = apply_policy_to_metadata(metadata, run, content_decision)
    return metadata


def validate_metadata(metadata: dict) -> None:
    required = {"title", "description", "tags", "attempt_id", "score", "human_record", "wall", "video_path"}
    if set(metadata) != required:
        raise ValueError("Metadatenfelder sind ungueltig")
    if not metadata["title"] or len(metadata["title"]) > 100:
        raise ValueError("YouTube-Titel fehlt oder ist zu lang")
    if not isinstance(metadata["tags"], list) or not metadata["attempt_id"]:
        raise ValueError("Tags oder attempt_id sind ungueltig")
    if metadata["human_record"] != HUMAN_RECORD or metadata["wall"] != WALL:
        raise ValueError("Rekord- oder Wall-Wert ist ungueltig")
    if f"BOT SCORE: {metadata['score']}" not in metadata["description"]:
        raise ValueError("Beschreibung widerspricht dem Score")
    if not Path(metadata["video_path"]).is_file():
        raise ValueError("Video aus Metadaten fehlt")


def write_metadata(metadata: dict, path: Path) -> Path:
    validate_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

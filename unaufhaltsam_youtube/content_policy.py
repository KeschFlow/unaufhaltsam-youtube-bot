"""Strict UNAUFHALTSAM-only content policy derived from real telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .database import Database
from .metadata import HUMAN_RECORD, WALL, result_hook
from .models import RunResult
from .performance import hook_type

ALLOWED_FORMATS = {"FAILS", "THE_WALL", "SCORES", "GAMEPLAY", "SYSTEM_DATA"}
BANNED_PHRASES = {
    "motivation", "motivationscoach", "mindset coaching", "fake player",
    "fake gameplay", "eddie content", "eddie-content",
}


@dataclass(frozen=True)
class ContentDecision:
    content_format: str
    event: str
    hook_type: str
    hook: str
    title: str
    visualization: str
    supporting_fact: str
    new_bot_best: bool

    def as_dict(self) -> dict:
        return asdict(self)


def decide_content(database: Database, run: RunResult) -> ContentDecision:
    with database.connection() as db:
        previous = db.execute(
            "SELECT MAX(reached_boxes) best,COUNT(*) runs FROM runs "
            "WHERE timestamp < ? AND attempt_id<>?", (run.timestamp, run.attempt_id),
        ).fetchone()
        same_score = db.execute(
            "SELECT COUNT(*) count FROM runs WHERE reached_boxes=? AND failed=1",
            (run.reached_boxes,),
        ).fetchone()["count"]
        average = db.execute(
            "SELECT AVG(duration_seconds) average FROM runs WHERE attempt_id<>?",
            (run.attempt_id,),
        ).fetchone()["average"]
    previous_best = int(previous["best"]) if previous["best"] is not None else -1
    new_best = run.reached_boxes > previous_best
    hook = result_hook(run.reached_boxes, run.completed)
    typed_hook = hook_type(run.reached_boxes, run.completed)

    if run.completed or run.wall_reached:
        content_format = "THE_WALL"
        event = "level_30_broken" if run.completed else "wall_reached"
        title = (
            "LEVEL 30 tatsächlich gebrochen – echter Bot-Run"
            if run.completed else f"Die Wall erreicht: {run.reached_boxes} echte Boxen"
        )
        visualization = "wall_result_gameplay"
        fact = f"Der reale Run erreichte {run.reached_boxes}; Wall: {WALL}."
    elif new_best and previous_best >= 0:
        content_format = "SCORES"
        event = "new_bot_best"
        title = f"Neuer Bot-Bestwert: {run.reached_boxes} echte Boxen"
        visualization = "score_first_gameplay"
        fact = f"Vorheriger Bot-Bestwert: {previous_best}; neuer Wert: {run.reached_boxes}."
    elif same_score >= 3:
        content_format = "SYSTEM_DATA"
        event = "repeated_failure_score"
        title = f"System Data: {same_score} echte Fails bei {run.reached_boxes} Boxen"
        visualization = "telemetry_then_gameplay"
        fact = f"SQLite enthält {same_score} reale Fehlversuche bei {run.reached_boxes} Boxen."
    elif average and run.duration_seconds > max(float(average) * 1.5, 60.0):
        content_format = "SYSTEM_DATA"
        event = "unusually_long_run"
        title = f"Ungewöhnlich langer Bot-Run: {run.duration_seconds:.1f} Sekunden"
        visualization = "duration_then_gameplay"
        fact = f"Reale Laufzeit: {run.duration_seconds:.1f} Sekunden."
    elif run.failed:
        content_format = "FAILS"
        event = "real_failed_attempt"
        title = f"BOT FAILED bei {run.reached_boxes} Boxen – echter Versuch"
        visualization = "failure_moment_gameplay"
        fact = f"Der reale Run endete bei {run.reached_boxes} Boxen."
    elif run.reached_boxes >= HUMAN_RECORD - 6:
        content_format = "SCORES"
        event = "high_score_near_record"
        title = f"Bot-Score {run.reached_boxes} – Human Record {HUMAN_RECORD}"
        visualization = "record_comparison_gameplay"
        fact = f"Bot-Score: {run.reached_boxes}; Human Record: {HUMAN_RECORD}."
    else:
        content_format = "GAMEPLAY"
        event = "raw_real_run"
        title = f"Echter UNAUFHALTSAM Bot-Run: {run.reached_boxes} Boxen"
        visualization = "raw_gameplay_score_overlay"
        fact = f"Unveränderter realer Bot-Run mit Score {run.reached_boxes}."

    decision = ContentDecision(
        content_format, event, typed_hook, hook, title, visualization, fact, new_best
    )
    validate_decision(run, decision)
    return decision


def validate_decision(run: RunResult, decision: ContentDecision) -> None:
    if decision.content_format not in ALLOWED_FORMATS:
        raise ValueError("Content-Format ist nicht erlaubt")
    combined = f"{decision.title} {decision.hook} {decision.supporting_fact}".lower()
    if any(phrase in combined for phrase in BANNED_PHRASES):
        raise ValueError("Verbotener Content-Typ erkannt")
    if str(run.reached_boxes) not in f"{decision.title} {decision.supporting_fact}":
        raise ValueError("Content ist nicht mit dem echten Score belegt")
    if decision.hook != result_hook(run.reached_boxes, run.completed):
        raise ValueError("Hook widerspricht dem echten Run")


def apply_policy_to_metadata(metadata: dict, run: RunResult, decision: ContentDecision) -> dict:
    validate_decision(run, decision)
    if metadata.get("attempt_id") != run.attempt_id or metadata.get("score") != run.reached_boxes:
        raise ValueError("Metadata und echter Run stimmen nicht ueberein")
    metadata = dict(metadata)
    metadata["title"] = decision.title
    marker = "\n\nUNAUFHALTSAM CONTENT FORMAT:"
    description = metadata["description"].split(marker, 1)[0]
    metadata["description"] = (
        f"{description}{marker} {decision.content_format}\n"
        f"REAL RUN EVIDENCE: {decision.supporting_fact}"
    )
    metadata["tags"] = list(dict.fromkeys([*metadata["tags"], decision.content_format]))
    return metadata


def validate_policy_output(metadata: dict, run: RunResult, decision: ContentDecision) -> None:
    validate_decision(run, decision)
    combined = f"{metadata.get('title', '')} {metadata.get('description', '')}".lower()
    if any(phrase in combined for phrase in BANNED_PHRASES):
        raise ValueError("Verbotener Inhalt in Metadata")
    if f"BOT SCORE: {run.reached_boxes}" not in metadata.get("description", ""):
        raise ValueError("Echter Bot-Score fehlt in Metadata")
    if decision.content_format not in metadata.get("description", ""):
        raise ValueError("Content-Format fehlt in Metadata")

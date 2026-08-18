"""Transparent content features, ratings, and queue priority rules."""

from __future__ import annotations

import json
import math
from typing import Any

from .metadata import HUMAN_RECORD, WALL


HOOK_BASE = {
    "BOT_FAILED_HUMAN_LEADS": 20.0,
    "BOT_TIES_HUMAN_RECORD": 60.0,
    "BOT_BEATS_HUMAN_RECORD": 72.0,
    "WALL_REACHED": 88.0,
    "LEVEL_30_BROKEN": 100.0,
}


def hook_type(score: int, completed: bool) -> str:
    if completed:
        return "LEVEL_30_BROKEN"
    if score >= WALL:
        return "WALL_REACHED"
    if score > HUMAN_RECORD:
        return "BOT_BEATS_HUMAN_RECORD"
    if score == HUMAN_RECORD:
        return "BOT_TIES_HUMAN_RECORD"
    return "BOT_FAILED_HUMAN_LEADS"


def title_variant_from_title(title: str) -> str:
    normalized = title.upper()
    if normalized.startswith("BOT VS. MENSCH"):
        return "duel_first"
    if normalized[:1].isdigit():
        return "score_first"
    return "result_first"


def rate_performance(features: dict[str, Any]) -> tuple[float, str]:
    """Return a 0-100 rating with an auditable component explanation."""
    hook = str(features["hook_type"])
    score = int(features.get("score") or 0)
    duration = float(features.get("duration_seconds") or 0)
    new_best = bool(features.get("new_best"))
    components: dict[str, float] = {
        "hook_base": HOOK_BASE.get(hook, 10.0) * 0.45,
        "score": min(score / WALL, 1.0) * 20.0,
        "long_run": min(max(duration - 20.0, 0.0) / 100.0, 1.0) * 5.0,
        "new_best": 8.0 if new_best else 0.0,
    }
    views_24h = features.get("views_24h")
    if views_24h is not None:
        components["views_24h"] = min(math.log10(max(int(views_24h), 0) + 1) / 5, 1) * 8
    average_percentage = features.get("average_percentage_viewed")
    if average_percentage is not None:
        components["retention"] = min(max(float(average_percentage), 0) / 100, 1) * 8
    ctr = features.get("ctr")
    if ctr is not None:
        components["ctr"] = min(max(float(ctr), 0) / 10, 1) * 3
    subscribers = features.get("subscribers_gained")
    if subscribers is not None:
        components["subscribers"] = min(max(int(subscribers), 0) / 20, 1) * 3
    rating = round(min(sum(components.values()), 100.0), 2)
    return rating, json.dumps(components, sort_keys=True)


def queue_priority(
    score: int, completed: bool, new_best: bool, performance_rating: float | None
) -> tuple[float, str]:
    if completed:
        tier, reason = 7000, "level_30_broken"
    elif score >= WALL:
        tier, reason = 6000, "wall_reached"
    elif new_best:
        tier, reason = 5000, "new_bot_best"
    elif score > HUMAN_RECORD:
        tier, reason = 4000, "human_record_beaten"
    elif score == HUMAN_RECORD:
        tier, reason = 3000, "human_record_tied"
    elif score >= 20:
        tier, reason = 2000, "high_score_near_human_record"
    else:
        tier, reason = 1000, "ordinary_real_run"
    learned = float(performance_rating or 0) * 0.1
    return round(tier + score * 10 + learned, 2), reason

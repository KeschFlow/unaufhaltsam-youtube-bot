"""Pipeline data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

BOT_HARD_CAP = 15
BOT_MIN_PUBLISH_SCORE = 10


@dataclass(frozen=True)
class RunResult:
    attempt_id: str
    timestamp: str
    reached_boxes: int
    rounds_completed: int
    duration_seconds: float
    failed: bool
    wall_reached: bool
    completed: bool
    recording_path: str
    strategy: str = "privileged_dom_identity"
    leaderboard_untouched: bool = True
    cap_reached: bool = False

    @property
    def is_publishable(self) -> bool:
        return BOT_MIN_PUBLISH_SCORE <= self.reached_boxes <= BOT_HARD_CAP

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunResult":
        values: dict[str, Any] = {}
        for field in fields(cls):
            if field.name in data:
                values[field.name] = data[field.name]
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.attempt_id or self.reached_boxes < 0 or self.rounds_completed < 0:
            raise ValueError("Ungueltiges Run-Ergebnis")
        if self.reached_boxes > BOT_HARD_CAP:
            raise ValueError(
                f"HARD CAP ERROR: Bot-Score {self.reached_boxes} ueberschreitet {BOT_HARD_CAP}"
            )
        if self.cap_reached and self.reached_boxes != BOT_HARD_CAP:
            raise ValueError("CAP_REACHED ist nur bei exakt 15 Boxen zulaessig")
        if self.duration_seconds < 0 or not Path(self.recording_path).is_file():
            raise ValueError("Run-Aufzeichnung fehlt oder Dauer ist ungueltig")
        if not self.leaderboard_untouched:
            raise ValueError("Leaderboard-Guardrail wurde ausgeloest")

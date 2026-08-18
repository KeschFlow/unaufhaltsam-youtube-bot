"""Pipeline data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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
    strategy: str = "visual_dom_tracking"
    leaderboard_untouched: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunResult":
        return cls(**{key: data[key] for key in cls.__dataclass_fields__})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.attempt_id or self.reached_boxes < 0 or self.rounds_completed < 0:
            raise ValueError("Ungueltiges Run-Ergebnis")
        if self.duration_seconds < 0 or not Path(self.recording_path).is_file():
            raise ValueError("Run-Aufzeichnung fehlt oder Dauer ist ungueltig")
        if not self.leaderboard_untouched:
            raise ValueError("Leaderboard-Guardrail wurde ausgeloest")

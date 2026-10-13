"""Pipeline data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
import hashlib
from typing import Any

BOT_HARD_CAP = 30
BOT_MIN_PUBLISH_SCORE = 0


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
    strategy: str = "visual_v1"
    leaderboard_untouched: bool = True
    cap_reached: bool = False
    fairness_passed: bool = False
    evidence_path: str = ""
    evidence_sha256: str = ""
    recording_sha256: str = ""
    game_url: str = ""
    bot_version: str = ""
    browser_version: str = ""
    runtime_version: str = ""
    runner_sha256: str = ""
    vision_sha256: str = ""

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
            raise ValueError("CAP_REACHED ist nur bei exakt 30 Boxen zulaessig")
        if self.duration_seconds < 0 or not Path(self.recording_path).is_file():
            raise ValueError("Run-Aufzeichnung fehlt oder Dauer ist ungueltig")
        if not self.leaderboard_untouched:
            raise ValueError("Leaderboard-Guardrail wurde ausgeloest")
        if self.strategy != "visual_v1" or not self.fairness_passed or self.bot_version != "AI-vs-THE-WALL-V1":
            raise ValueError("FAIRNESS_FAIL: kein attestierter visueller V1-Lauf")
        for filename, expected in ((self.recording_path, self.recording_sha256), (self.evidence_path, self.evidence_sha256)):
            if not filename or not Path(filename).is_file() or len(expected) != 64:
                raise ValueError("FAIRNESS_FAIL: Video oder Eventlog fehlt")
            if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
                raise ValueError("FAIRNESS_FAIL: Beweis-Hash stimmt nicht")
        for filename, expected in ((Path(__file__).with_name("browser_runner.mjs"), self.runner_sha256),
                                   (Path(__file__).with_name("screen_vision.py"), self.vision_sha256)):
            if len(expected) != 64 or hashlib.sha256(filename.read_bytes()).hexdigest() != expected:
                raise ValueError("FAIRNESS_FAIL: Runner-Version stimmt nicht")

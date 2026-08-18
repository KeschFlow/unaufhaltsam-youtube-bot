"""Windows process lock shared by scheduler, CLI, healthcheck, and live switch."""

from __future__ import annotations

import contextlib
import msvcrt
from pathlib import Path
from typing import Iterator


class AutopilotAlreadyRunning(RuntimeError):
    pass


@contextlib.contextmanager
def autopilot_lock(output_dir: Path) -> Iterator[None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "autopilot.lock"
    handle = path.open("a+b")
    locked = False
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
        except OSError as exc:
            raise AutopilotAlreadyRunning("Eine Autopilot-Instanz laeuft bereits") from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def is_autopilot_running(output_dir: Path) -> bool:
    try:
        with autopilot_lock(output_dir):
            return False
    except AutopilotAlreadyRunning:
        return True

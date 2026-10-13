"""Cross-platform process lock shared by scheduler, CLI, healthcheck, and live switch."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class AutopilotAlreadyRunning(RuntimeError):
    pass


def _try_lock(handle) -> None:
    if os.name == "nt":
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise AutopilotAlreadyRunning("Eine Autopilot-Instanz laeuft bereits") from exc
        return

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise AutopilotAlreadyRunning("Eine Autopilot-Instanz laeuft bereits") from exc


def _unlock(handle) -> None:
    if os.name == "nt":
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
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
        _try_lock(handle)
        locked = True
        yield
    finally:
        if locked:
            _unlock(handle)
        handle.close()


def is_autopilot_running(output_dir: Path) -> bool:
    try:
        with autopilot_lock(output_dir):
            return False
    except AutopilotAlreadyRunning:
        return True

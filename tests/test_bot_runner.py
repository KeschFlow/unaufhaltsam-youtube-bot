from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from unaufhaltsam_youtube.bot_runner import BrowserGameError, run_bot
from unaufhaltsam_youtube.config import Config


class BotRunnerTests(unittest.TestCase):
    def test_process_timeout_is_retryable_browser_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(
                Config.from_env(), output_dir=root, db_file=root / "test.sqlite3",
                run_timeout_seconds=10,
            )
            with patch(
                "unaufhaltsam_youtube.bot_runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["node"], 100),
            ):
                with self.assertRaises(BrowserGameError):
                    run_bot(config, "attempt-timeout")


if __name__ == "__main__":
    unittest.main()

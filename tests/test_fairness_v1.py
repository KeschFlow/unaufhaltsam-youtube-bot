import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from unaufhaltsam_youtube.models import RunResult
from unaufhaltsam_youtube.screen_vision import observe
from unaufhaltsam_youtube.database import Database
from unaufhaltsam_youtube.config import Config
from unaufhaltsam_youtube.publisher import PublishError, publish


class FairnessV1Tests(unittest.TestCase):
    def test_perception_finds_focus_and_box_from_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frame.png"
            image = Image.new("RGB", (1080, 1920), "black")
            draw = ImageDraw.Draw(image)
            draw.rectangle((400, 800, 481, 881), fill=(128, 0, 128))
            draw.rectangle((496, 800, 577, 881), fill="white")
            image.save(path)
            result = observe(str(path))
            self.assertEqual(len(result["cells"]), 2)
            self.assertTrue(all(abs(x - expected_x) <= 2 and abs(y - 841) <= 2 for (x, y), expected_x in zip(result["cells"], (441, 537))))
            self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(result["focus"], (441, 841))))

    def test_privileged_or_unattested_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            recording = Path(folder) / "game.webm"
            recording.write_bytes(b"video")
            run = RunResult(
                attempt_id="test", timestamp=datetime.now(UTC).isoformat(),
                reached_boxes=2, rounds_completed=0, duration_seconds=1,
                failed=True, wall_reached=False, completed=False,
                recording_path=str(recording), strategy="privileged_dom_identity",
            )
            with self.assertRaisesRegex(ValueError, "FAIRNESS_FAIL"):
                run.validate()
            with self.assertRaisesRegex(ValueError, "FAIRNESS_FAIL"):
                replace(run, strategy="visual_v1").validate()

    def test_runner_source_does_not_read_game_identity(self):
        source = (Path(__file__).parents[1] / "unaufhaltsam_youtube" / "browser_runner.mjs").read_text()
        for prohibited in ("getAttribute(", "data-id=", "page.evaluate(", "#boxesValue", ".focus\")"):
            self.assertNotIn(prohibited, source)
        self.assertIn("page.screenshot", source)
        self.assertIn("page.mouse.click", source)

    def test_public_upload_rejects_legacy_evidence_before_oauth(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            db = Database(root / "test.sqlite3")
            metadata = {
                "title": "Bot Demo", "description": "BOT SCORE: 12", "tags": [],
                "attempt_id": "legacy", "score": 12, "human_record": 26,
                "wall": 30, "video_path": str(video),
            }
            with self.assertRaisesRegex(PublishError, "FAIRNESS_FAIL"):
                publish(metadata, "public", db)

    def test_privileged_strategy_cannot_start(self):
        with patch.dict("os.environ", {"BOT_STRATEGY": "privileged_dom_identity", "PUBLISH_MODE": "dry_run"}):
            with self.assertRaisesRegex(ValueError, "FAIRNESS_FAIL"):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()

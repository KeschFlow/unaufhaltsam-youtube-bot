from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import imageio_ffmpeg

from unaufhaltsam_youtube.database import Database
from unaufhaltsam_youtube.config import Config
from unaufhaltsam_youtube.metadata import (
    build_metadata,
    result_hook,
    validate_metadata,
    write_metadata,
)
from unaufhaltsam_youtube.analytics_worker import run_analytics
from unaufhaltsam_youtube.content_policy import ContentDecision, decide_content, validate_decision
from unaufhaltsam_youtube.performance import queue_priority, rate_performance
from unaufhaltsam_youtube.models import RunResult
from unaufhaltsam_youtube.publisher import PublishError, publish
from unaufhaltsam_youtube.queue import queue_count, write_queue_item
from unaufhaltsam_youtube.strategy_worker import rank_queue
from unaufhaltsam_youtube.renderer import render_short


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.recording = self.root / "run.webm"
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
             "color=c=black:s=360x640:d=0.5:r=30", "-an", str(self.recording)],
            check=True, capture_output=True,
        )
        self.run = RunResult(
            attempt_id="attempt-test", timestamp=datetime.now(UTC).isoformat(),
            reached_boxes=12, rounds_completed=10, duration_seconds=5.0,
            failed=True, wall_reached=False, completed=False,
            recording_path=str(self.recording), leaderboard_untouched=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_result_hooks_are_truthful(self) -> None:
        self.assertIn("HUMAN RECORD REMAINS SEPARATE", result_hook(10))
        self.assertEqual(result_hook(15, cap_reached=True), "BOT RUN CAP REACHED AT 15.")
        self.assertEqual(result_hook(30, True), "LEVEL 30 BROKEN.")

    def test_sqlite_schema_and_duplicate_run_guard(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        with self.assertRaises(Exception):
            db.add_run(self.run)
        self.assertEqual(db.next_attempt_number(), 2)

    def test_renderer_metadata_and_publisher_dry_run(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        video = render_short(self.run, 1, self.root / "short.mp4")
        self.assertGreater(video.stat().st_size, 0)
        metadata = build_metadata(self.run, 1, video)
        validate_metadata(metadata)
        metadata_path = write_metadata(metadata, self.root / "metadata.json")
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["score"], 12)
        db.add_video(self.run.attempt_id, video, metadata_path)
        response = publish(metadata, "dry_run", db)
        self.assertEqual(response["status"], "validated_no_upload")
        self.assertFalse(db.has_public_upload(self.run.attempt_id))

    def test_public_duplicate_is_rejected_before_oauth(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        video = render_short(self.run, 1, self.root / "short.mp4")
        metadata = build_metadata(self.run, 1, video)
        db.add_upload(self.run.attempt_id, "public", "published", "youtube-id", {})
        with self.assertRaises(PublishError):
            publish(metadata, "public", db)

    def test_metadata_rejects_score_mismatch(self) -> None:
        video = render_short(self.run, 1, self.root / "short.mp4")
        metadata = build_metadata(self.run, 1, video)
        metadata["score"] = 99
        with self.assertRaises(ValueError):
            validate_metadata(metadata)

    def test_persistent_queue_grows(self) -> None:
        video = render_short(self.run, 1, self.root / "short.mp4")
        metadata = build_metadata(self.run, 1, video)
        metadata_path = write_metadata(metadata, self.root / "metadata.json")
        write_queue_item(
            self.root, self.run.attempt_id, self.run.reached_boxes, True,
            video, metadata_path, "dry_run", "ready",
        )
        self.assertEqual(queue_count(self.root), 1)

    def test_performance_schema_contains_learning_fields(self) -> None:
        db = Database(self.root / "test.sqlite3")
        with db.connection() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(performance)")}
        required = {
            "video_id", "published_at", "views_24h", "views_48h", "likes_24h",
            "likes_48h", "comments_24h", "comments_48h", "average_view_duration",
            "average_percentage_viewed", "impressions", "ctr", "subscribers_gained",
            "score", "human_record", "wall", "hook_type", "title_variant",
            "publish_score", "performance_rating",
        }
        self.assertTrue(required.issubset(columns))

    def test_rating_and_queue_priority_are_transparent(self) -> None:
        rating, explanation = rate_performance({
            "hook_type": "BOT_BEATS_HUMAN_RECORD", "score": 27,
            "duration_seconds": 45, "new_best": True, "views_24h": 1000,
            "average_percentage_viewed": 80, "ctr": 5, "subscribers_gained": 2,
        })
        self.assertGreater(rating, 50)
        self.assertIn("hook_base", json.loads(explanation))
        wall, _ = queue_priority(30, False, False, None)
        best, _ = queue_priority(20, False, True, None)
        ordinary, _ = queue_priority(10, False, False, None)
        self.assertGreater(wall, best)
        self.assertGreater(best, ordinary)

    def test_analytics_dry_run_marks_metrics_pending_without_api(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        video = render_short(self.run, 1, self.root / "short.mp4")
        metadata = build_metadata(self.run, 1, video)
        metadata_path = write_metadata(metadata, self.root / "metadata.json")
        db.add_video(self.run.attempt_id, video, metadata_path)
        result = run_analytics(None, db, "dry_run")
        self.assertFalse(result["api_calls"])
        self.assertEqual(result["videos"][0]["metrics_status"], "pending")

    def test_public_upload_is_reserved_and_finalized_atomically(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        db.reserve_public_upload(self.run.attempt_id)
        with self.assertRaises(RuntimeError):
            db.reserve_public_upload(self.run.attempt_id)
        db.finalize_public_upload(self.run.attempt_id, "video-id", {"id": "video-id"})
        self.assertTrue(db.has_public_upload(self.run.attempt_id))
        with db.connection() as connection:
            state = connection.execute(
                "SELECT value FROM publication_state WHERE key='first_public_upload_completed'"
            ).fetchone()
        self.assertEqual(state["value"], "true")

    def test_next_public_upload_can_be_reserved_after_previous_completed(self) -> None:
        db = Database(self.root / "test.sqlite3")
        first = self.run
        second = replace(self.run, attempt_id="attempt-next")
        db.add_run(first)
        db.add_run(second)
        db.reserve_public_upload(first.attempt_id)
        db.finalize_public_upload(first.attempt_id, "video-first", {"id": "video-first"})
        db.reserve_public_upload(second.attempt_id)
        db.finalize_public_upload(second.attempt_id, "video-next", {"id": "video-next"})
        self.assertTrue(db.has_public_upload(second.attempt_id))

    def test_dry_run_does_not_consume_public_daily_limit(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        db.add_upload(self.run.attempt_id, "dry_run", "dry_run_ok", None, {})
        self.assertEqual(db.count_public_uploads_today(), 0)

    def test_queue_limit_prunes_lowest_priority_without_deleting_run(self) -> None:
        db_path = self.root / "test.sqlite3"
        db = Database(db_path)
        low = self.run
        high = RunResult(
            attempt_id="attempt-high", timestamp=datetime.now(UTC).isoformat(),
            reached_boxes=15, rounds_completed=13, duration_seconds=30,
            failed=True, wall_reached=False, completed=False,
            recording_path=str(self.recording), leaderboard_untouched=True,
        )
        for number, run in enumerate((low, high), 1):
            db.add_run(run)
            video = render_short(run, number, self.root / "videos" / f"{run.attempt_id}.mp4")
            metadata = build_metadata(run, number, video)
            metadata_path = write_metadata(metadata, self.root / "metadata" / f"{run.attempt_id}.json")
            db.add_video(run.attempt_id, video, metadata_path)
            write_queue_item(
                self.root, run.attempt_id, run.reached_boxes, True, video,
                metadata_path, "dry_run", "ready",
            )
        run_analytics(None, db, "dry_run")
        config = replace(
            Config.from_env(), output_dir=self.root, db_file=db_path, max_queue_size=1,
            publish_mode="dry_run",
        )
        ranked = rank_queue(config, db)
        self.assertEqual([candidate.attempt_id for candidate in ranked], ["attempt-high"])
        with db.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_public_ranking_recomputes_eligibility_from_current_threshold(self) -> None:
        db_path = self.root / "test.sqlite3"
        db = Database(db_path)
        db.add_run(self.run)
        video = render_short(self.run, 1, self.root / "videos" / "short.mp4")
        metadata = build_metadata(self.run, 1, video)
        metadata_path = write_metadata(metadata, self.root / "metadata" / "metadata.json")
        db.add_video(self.run.attempt_id, video, metadata_path)
        queue_path = write_queue_item(
            self.root, self.run.attempt_id, self.run.reached_boxes, True,
            video, metadata_path, "dry_run", "dry_run_validated",
        )
        config = replace(
            Config.from_env(), output_dir=self.root, db_file=db_path,
            publish_mode="public", minimum_publish_score=self.run.reached_boxes + 1,
        )
        self.assertEqual(rank_queue(config, db), [])
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertFalse(payload["eligible"])
        self.assertEqual(payload["status"], "below_threshold")

    def test_hard_cap_and_publish_window(self) -> None:
        capped = replace(self.run, reached_boxes=15, rounds_completed=13, failed=False, cap_reached=True)
        capped.validate()
        self.assertTrue(capped.is_publishable)
        self.assertFalse(replace(self.run, reached_boxes=9).is_publishable)
        with self.assertRaises(ValueError):
            replace(self.run, reached_boxes=16).validate()

    def test_renderer_blocks_outside_publish_window(self) -> None:
        from unaufhaltsam_youtube.renderer import RenderError
        with self.assertRaises(RenderError):
            render_short(replace(self.run, reached_boxes=9), 1, self.root / "low.mp4")
        with self.assertRaises(RenderError):
            render_short(replace(self.run, reached_boxes=16), 1, self.root / "high.mp4")

    def test_content_policy_uses_real_run_and_blocks_forbidden_format(self) -> None:
        db = Database(self.root / "test.sqlite3")
        db.add_run(self.run)
        decision = decide_content(db, self.run)
        self.assertIn(decision.content_format, {"FAILS", "SCORES", "GAMEPLAY", "SYSTEM_DATA", "THE_WALL"})
        self.assertIn(str(self.run.reached_boxes), decision.supporting_fact)
        forbidden = ContentDecision(
            "MOTIVATION", "generic", decision.hook_type, decision.hook,
            "Generic motivation", "ai_filler", "No real evidence", False,
        )
        with self.assertRaises(ValueError):
            validate_decision(self.run, forbidden)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_config
import drain
import pipeline


class SummaryConfigurationTests(unittest.TestCase):
    def test_detail_levels_are_distinct(self):
        self.assertEqual(pipeline.normalize_summary_detail("Simple"), "simple")
        self.assertEqual(pipeline.normalize_summary_detail("detailed"), "detailed")
        self.assertEqual(pipeline.normalize_summary_detail("complex"), "complex")
        self.assertLess(
            pipeline.SUMMARY_DETAILS["simple"]["max_tokens"],
            pipeline.SUMMARY_DETAILS["complex"]["max_tokens"],
        )

    def test_unknown_pipeline_detail_is_rejected(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline.normalize_summary_detail("enormous")

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"CONTENT_SUMMARIZER_DATA_DIR": tmp}, clear=False
            ):
                app_config.save_settings(
                    {
                        "summary_folder": str(Path(tmp) / "Drive"),
                        "summary_detail": "detailed",
                        "auto_start": False,
                    }
                )
                loaded = app_config.load_settings()
        self.assertEqual(loaded["summary_detail"], "detailed")
        self.assertFalse(loaded["auto_start"])
        self.assertTrue(str(loaded["summary_folder"]).endswith("Drive"))


class OutputTests(unittest.TestCase):
    def test_filenames_include_video_id_and_do_not_collide(self):
        first = drain.summary_filename("Same title", "abc123")
        second = drain.summary_filename("Same title", "xyz789")
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("--abc123.txt"))

    def test_write_summary_creates_plain_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = drain.write_summary(tmp, "summary.txt", "Title\nURL\n\nTL;DR\nText\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "Title\nURL\n\nTL;DR\nText\n")
            self.assertFalse((Path(tmp) / "summary.txt.tmp").exists())

    def test_existing_repository_markdown_is_imported_as_text(self):
        class FakeGitHub:
            def files_in_directory(self, _path):
                return [
                    {"type": "file", "name": "old.md", "path": "summaries/old.md"},
                    {"type": "file", "name": "new.txt", "path": "summaries/new.txt"},
                ]

            def file_content(self, path):
                return f"content from {path}"

        with tempfile.TemporaryDirectory() as tmp:
            count = drain.import_repository_summaries(FakeGitHub(), tmp)
            self.assertEqual(count, 2)
            self.assertTrue((Path(tmp) / "old.txt").is_file())
            self.assertTrue((Path(tmp) / "new.txt").is_file())


class SafetyTests(unittest.TestCase):
    def test_failure_comments_are_bounded_and_single_line(self):
        message = drain.failure_message(RuntimeError("bad\n" + "x" * 3000))
        self.assertLessEqual(len(message), 1500)
        self.assertNotIn("\n", message)

    def test_youtube_url_allowlist(self):
        accepted = (
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://m.youtube.com/shorts/abc",
        )
        for url in accepted:
            self.assertEqual(pipeline.validate_youtube_url(url), url)

    def test_non_youtube_and_local_urls_are_rejected(self):
        for url in ("https://example.com/video", "http://127.0.0.1/admin"):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.validate_youtube_url(url)

    def test_yt_dlp_timeout_becomes_pipeline_error(self):
        with mock.patch(
            "pipeline.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["yt-dlp"], 1),
        ):
            with self.assertRaisesRegex(pipeline.PipelineError, "timed out"):
                pipeline._run_yt_dlp([], timeout=1)

    def test_frozen_yt_dlp_uses_bundled_entrypoint(self):
        with mock.patch.object(pipeline.sys, "frozen", True, create=True):
            with mock.patch("yt_dlp.main", side_effect=lambda _args: print("2026.test")):
                result = pipeline._run_yt_dlp(["--version"], timeout=1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "2026.test")


if __name__ == "__main__":
    unittest.main()

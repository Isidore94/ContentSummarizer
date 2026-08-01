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


class CustomPromptTests(unittest.TestCase):
    def test_prompt_survives_a_round_trip_through_the_issue_body(self):
        prompt = "Focus on the investing advice and list every ticker mentioned."
        body = drain.build_issue_body(prompt)
        self.assertIn(drain.PROMPT_MARKER, body)
        self.assertEqual(drain.parse_issue_prompt(body), prompt)

    def test_prompt_containing_a_code_fence_round_trips(self):
        prompt = "Summarize, then show:\n```python\nprint('hi')\n```\nNothing else."
        self.assertEqual(
            drain.parse_issue_prompt(drain.build_issue_body(prompt)), prompt
        )

    def test_empty_prompt_produces_no_issue_body(self):
        self.assertIsNone(drain.build_issue_body("   "))
        self.assertIsNone(drain.build_issue_body(None))
        self.assertEqual(drain.parse_issue_prompt(None), "")
        self.assertEqual(drain.parse_issue_prompt(""), "")

    def test_unmarked_body_is_taken_as_the_prompt(self):
        # A prompt typed straight into the iOS Shortcut or on github.com.
        self.assertEqual(drain.parse_issue_prompt("  Just the key numbers.  "),
                         "Just the key numbers.")

    def test_long_prompts_are_bounded(self):
        prompt = pipeline.normalize_custom_prompt("x" * 5_000)
        self.assertLessEqual(len(prompt), pipeline.MAX_CUSTOM_PROMPT_CHARS + 1)

    def test_custom_prompt_reaches_the_model_and_plain_summaries_are_unchanged(self):
        with_prompt = pipeline._summary_prompt("hello", "simple", "Only the numbers.")
        self.assertIn("Only the numbers.", with_prompt)
        self.assertIn("hello", with_prompt)

        without = pipeline._summary_prompt("hello", "simple")
        self.assertNotIn("ADDITIONAL INSTRUCTIONS", without)


class GitHubErrorTests(unittest.TestCase):
    """GitHub answers outages with a full HTML page; it must not reach the UI."""

    class _Resp:
        def __init__(self, text, status_code=503, payload=None):
            self.text = text
            self.status_code = status_code
            self.reason = "Service Unavailable"
            self.ok = False
            self._payload = payload

        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    UNICORN = (
        "<!DOCTYPE html>\n<!--\n\nHello future GitHubber! I bet you're here to "
        "remove those nasty inline styles,\nDRY up these templates and make 'em "
        "nice and re-usable, right?\n\nPlease, don't.\n\n-->\n<html>\n<head>\n"
        "<title>Unicorn! &middot; GitHub</title>\n<style>body { margin: 0; }</style>\n"
        "</head>\n<body>" + "<p>filler</p>" * 400 + "</body>\n</html>\n"
    )

    def test_html_outage_page_becomes_one_short_line(self):
        detail = drain.response_error(self._Resp(self.UNICORN))
        self.assertNotIn("GitHubber", detail)
        self.assertNotIn("<", detail)
        self.assertLessEqual(len(detail), 300)
        self.assertIn("outage", detail)

    def test_json_api_errors_keep_their_message(self):
        detail = drain.response_error(
            self._Resp('{"message": "Bad credentials"}', 401,
                       payload={"message": "Bad credentials"})
        )
        self.assertEqual(detail, "Bad credentials")

    def test_long_plain_text_error_is_truncated(self):
        detail = drain.response_error(self._Resp("boom " * 500))
        self.assertLessEqual(len(detail), 300)

    def test_gui_labels_never_grow_unbounded(self):
        import desktop_gui

        self.assertLessEqual(len(desktop_gui._one_line(self.UNICORN)), 160)
        self.assertNotIn("\n", desktop_gui._one_line(self.UNICORN))


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

    def test_prompts_are_learning_first(self):
        """Summaries must teach the content, not mention topics (Aaron's prime
        directive). Every level keeps its Bad/Good steering pair, the
        explain-without-watching test, and the custom-prompt-friendly wording."""
        self.assertIn("teach", pipeline._SUMMARY_SYSTEM)
        self.assertIn("without watching", pipeline._SUMMARY_SYSTEM)
        for detail, spec in pipeline.SUMMARY_DETAILS.items():
            text = spec["instructions"]
            self.assertIn("Bad (topic mention):", text, detail)
            self.assertIn("Good (the actual lesson):", text, detail)
            self.assertIn("without watching", text, detail)
            self.assertIn("CORE IDEA", text, detail)
            self.assertIn(
                "unless the requester's added instructions say otherwise",
                text,
                detail,
            )
            self.assertIn("never outside facts", text, detail)

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
                        "notify_sound": False,
                    }
                )
                loaded = app_config.load_settings()
        self.assertEqual(loaded["summary_detail"], "detailed")
        self.assertFalse(loaded["auto_start"])
        self.assertFalse(loaded["notify_sound"])
        self.assertTrue(str(loaded["summary_folder"]).endswith("Drive"))

    def test_settings_written_before_the_chime_option_still_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"CONTENT_SUMMARIZER_DATA_DIR": tmp}, clear=False
            ):
                app_config.settings_path().write_text(
                    '{"summary_folder": "X", "summary_detail": "simple",'
                    ' "auto_start": true}',
                    encoding="utf-8",
                )
                loaded = app_config.load_settings()
        self.assertTrue(loaded["notify_sound"])


class SummaryListTests(unittest.TestCase):
    """The GUI announces finished summaries, so its list helpers must be exact."""

    def test_only_unseen_files_count_as_new(self):
        import desktop_gui

        seen = {"old.txt"}
        self.assertEqual(
            desktop_gui._detect_new(seen, ["fresh.txt", "old.txt"]), ["fresh.txt"]
        )
        self.assertEqual(desktop_gui._detect_new(seen, ["old.txt"]), [])

    def test_filter_matches_every_word_in_any_order(self):
        import desktop_gui

        name = drain.summary_filename("Jordan Peterson on IQ", "abc123")
        self.assertTrue(desktop_gui._matches_filter(name, "peterson iq"))
        self.assertTrue(desktop_gui._matches_filter(name, "IQ jordan"))
        self.assertTrue(desktop_gui._matches_filter(name, ""))
        self.assertFalse(desktop_gui._matches_filter(name, "peterson finance"))

    def test_pretty_title_drops_the_video_id_suffix(self):
        """It must undo exactly what drain.summary_filename() builds."""
        import desktop_gui

        name = drain.summary_filename("The Big IQ Controversy", "dQw4w9WgXcQ")
        self.assertEqual(desktop_gui._pretty_title(name), "The big iq controversy")
        self.assertNotIn("dQw4w9WgXcQ", desktop_gui._pretty_title(name))
        self.assertEqual(desktop_gui._pretty_title("plain.txt"), "Plain")

    def test_ages_read_as_plain_english(self):
        import desktop_gui

        self.assertEqual(desktop_gui._human_age(5), "just now")
        self.assertEqual(desktop_gui._human_age(120), "2m ago")
        self.assertEqual(desktop_gui._human_age(7200), "2h ago")
        self.assertEqual(desktop_gui._human_age(86400 * 3), "3d ago")

    def test_clipboard_only_offers_a_real_youtube_link(self):
        import desktop_gui

        self.assertIsNotNone(
            desktop_gui._clipboard_youtube_url(
                "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  "
            )
        )
        self.assertIsNone(desktop_gui._clipboard_youtube_url("https://example.com/x"))
        self.assertIsNone(desktop_gui._clipboard_youtube_url(""))
        self.assertIsNone(
            desktop_gui._clipboard_youtube_url(
                "look at https://www.youtube.com/watch?v=dQw4w9WgXcQ ok"
            )
        )


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

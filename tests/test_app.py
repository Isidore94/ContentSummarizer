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


class WebRefreshTests(unittest.TestCase):
    """The LAN page can keep itself up to date, per browser.

    Everything the worker does happens elsewhere, so the page a phone is left open on
    goes stale silently: the summary lands, the queue empties, and the screen still
    shows the state from whenever it was loaded.
    """

    @classmethod
    def setUpClass(cls):
        import dashboard
        from fastapi.testclient import TestClient

        class _StubWorker:
            poll_seconds = 120
            gh = None

            def snapshot(self):
                return {
                    "state": "idle",
                    "last_poll": None,
                    "last_result": "",
                    "ok_total": 0,
                    "failed_total": 0,
                }

        cls.dashboard = dashboard
        dashboard.attach_worker(_StubWorker(), summary_dir=tempfile.mkdtemp())
        # The stub has no GitHub client; home() already renders the queue error path.
        cls.client = TestClient(dashboard.app)

    def test_refresh_choice_is_only_ever_one_of_the_offered_intervals(self):
        for good in self.dashboard.REFRESH_CHOICES:
            self.assertEqual(self.dashboard._refresh_seconds(str(good)), good)
        for bad in ("", "  ", "7", "-30", "abc", None, "99999999", "30; drop table"):
            self.assertEqual(self.dashboard._refresh_seconds(bad), 0)

    def test_page_is_static_until_auto_refresh_is_turned_on(self):
        page = self.client.get("/").text
        self.assertIn("Refresh now", page)  # the manual option is always there
        self.assertIn("Auto-refresh:", page)
        self.assertNotIn("location.reload", page)
        self.assertNotIn("http-equiv", page)

    def test_choosing_an_interval_makes_the_page_reload_itself(self):
        self.client.post("/settings/refresh", data={"seconds": "30"})
        page = self.client.get("/").text
        self.assertIn("location.reload", page)
        self.assertIn("refreshing in 30s", page)
        # A browser without JS still keeps up, via plain HTML.
        self.assertIn('<meta http-equiv="refresh" content="30">', page)

    def test_the_choice_survives_a_reload_and_can_be_turned_back_off(self):
        self.client.post("/settings/refresh", data={"seconds": "60"})
        self.assertEqual(self.client.cookies.get(self.dashboard.REFRESH_COOKIE), "60")
        self.assertIn("refreshing in 60s", self.client.get("/").text)

        self.client.post("/settings/refresh", data={"seconds": "0"})
        page = self.client.get("/").text
        self.assertNotIn("location.reload", page)
        self.assertNotIn("http-equiv", page)

    def test_a_tampered_cookie_cannot_turn_into_a_reload_loop(self):
        self.client.cookies.set(self.dashboard.REFRESH_COOKIE, "1")
        page = self.client.get("/").text
        self.assertNotIn("location.reload", page)
        self.client.cookies.delete(self.dashboard.REFRESH_COOKIE)

    def test_reloading_holds_while_someone_is_typing(self):
        """A page that reloaded under a half-typed URL would throw the URL away."""
        self.client.post("/settings/refresh", data={"seconds": "15"})
        page = self.client.get("/").text
        self.assertIn("paused while you type", page)
        self.assertIn("activeElement", page)
        self.client.post("/settings/refresh", data={"seconds": "0"})

    def test_a_summary_shows_its_title_as_a_heading_exactly_once(self):
        d = self.dashboard
        text = "The Big IQ Controversy\n\nCORE IDEA\nTests measure a narrow band.\n"
        self.assertEqual(
            d._body_without_title(text, "The Big IQ Controversy"),
            "CORE IDEA\nTests measure a narrow band.",
        )

    def test_a_summary_that_opens_in_prose_keeps_every_word(self):
        d = self.dashboard
        text = "IQ tests measure a narrow band.\nAnd a second line.\n"
        self.assertEqual(d._body_without_title(text, "Something else entirely"), text)
        # The detected title IS the first line here, so stripping it is correct and
        # must not also eat the line after it.
        self.assertEqual(
            d._body_without_title(text, "IQ tests measure a narrow band."),
            "And a second line.",
        )

    def test_the_gpu_badge_is_not_probed_on_every_reload(self):
        """A switched-off GPU PC costs a multi-second timeout. Paying it once per page
        load was tolerable; paying it every 15 seconds under auto-refresh is not."""
        self.dashboard._gpu_status["checked_at"] = 0.0
        with mock.patch(
            "dashboard.pipeline.gpu_node_online", return_value=True
        ) as probe:
            for _ in range(5):
                self.dashboard._gpu_online_cached()
        self.assertEqual(probe.call_count, 1)

    def test_other_pages_do_not_reload_themselves(self):
        """Auto-refresh belongs to the dashboard; a summary being read must sit still."""
        self.client.post("/settings/refresh", data={"seconds": "30"})
        for path in ("/search?q=test", "/s/nope"):
            page = self.client.get(path).text
            self.assertNotIn("location.reload", page, path)
        self.client.post("/settings/refresh", data={"seconds": "0"})


class TranscriptTests(unittest.TestCase):
    """The transcript is the whole text a summary is built from.

    Before this it existed only in memory for the length of one API call: a thin
    or wrong summary could never be checked against what was actually said, and
    a video that only needed its words could not be had without paying a model
    to rewrite them.
    """

    METADATA = {"id": "abc123", "title": "A Talk"}
    URL = "https://www.youtube.com/watch?v=abc123"

    def _patched(self, summarize):
        """A run with the network and the model stubbed out, shape intact."""
        return mock.patch.multiple(
            pipeline,
            get_metadata=lambda url: dict(self.METADATA),
            fetch_transcript=lambda url, force_whisper=False: {
                "text": "every word of it",
                "source": "manual captions",
            },
            summarize=summarize,
        )

    def test_a_mode_is_either_summarize_or_do_not(self):
        for value in ("summary", "Summary", " summarize ", None, ""):
            self.assertEqual(pipeline.normalize_output_mode(value), "summary")
        for value in ("raw", "RAW", "transcript", "transcript-only"):
            self.assertEqual(pipeline.normalize_output_mode(value), "raw")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.normalize_output_mode("summarise-ish")

    def test_raw_mode_never_calls_the_model(self):
        def refuse(*args, **kwargs):
            raise AssertionError("raw mode must not call the summarizer")

        with self._patched(refuse):
            result = pipeline.summarize_video(self.URL, mode="raw")
        self.assertEqual(result["mode"], "raw")
        self.assertIn("every word of it", result["text"])
        self.assertIn("A Talk", result["text"])

    def test_a_summary_still_carries_the_transcript_it_came_from(self):
        with self._patched(lambda transcript, **kwargs: "The gist."):
            result = pipeline.summarize_video(self.URL, mode="summary")
        self.assertIn("The gist.", result["text"])
        self.assertNotIn("every word of it", result["text"])  # a summary is a summary
        self.assertEqual(result["transcript"], "every word of it")
        self.assertIn("every word of it", result["transcript_text"])
        self.assertIn("manual captions", result["transcript_text"])

    def test_an_empty_transcript_fails_loudly_instead_of_summarizing_nothing(self):
        empty = mock.patch.multiple(
            pipeline,
            get_metadata=lambda url: dict(self.METADATA),
            fetch_transcript=lambda url, force_whisper=False: {
                "text": "   ",
                "source": "captions",
            },
        )
        with empty, self.assertRaises(pipeline.PipelineError):
            pipeline.summarize_video(self.URL)

    def test_the_mode_survives_the_trip_through_the_issue_body(self):
        self.assertEqual(drain.parse_issue_mode(drain.build_issue_body("", "raw")), "raw")
        # An unmarked issue - every one queued before today - still summarizes.
        self.assertEqual(drain.parse_issue_mode(None), "summary")
        self.assertEqual(drain.parse_issue_mode("just a note"), "summary")
        self.assertIsNone(drain.build_issue_body("", "summary"))

    def test_the_mode_marker_is_never_mistaken_for_a_prompt(self):
        self.assertEqual(drain.parse_issue_prompt(drain.build_issue_body("", "raw")), "")
        both = drain.build_issue_body("Only the numbers.", "raw")
        self.assertEqual(drain.parse_issue_mode(both), "raw")
        self.assertEqual(drain.parse_issue_prompt(both), "Only the numbers.")

    def test_an_unreadable_mode_summarizes_rather_than_dropping_the_video(self):
        self.assertEqual(
            drain.parse_issue_mode(drain.MODE_MARKER + " sideways"), "summary"
        )

    def test_a_transcript_is_kept_for_every_job_and_raw_ones_stay_local(self):
        commits = []

        class FakeGitHub:
            def commit_file(self, path, content, message):
                commits.append(path)

            def comment(self, number, body):
                self.body = body

            def close_issue(self, number):
                self.closed = number

        def fake_summarize_video(url, **kwargs):
            mode = kwargs["mode"]
            spoken = "line one. line two."
            return {
                "id": "abc123",
                "title": "A Talk",
                "mode": mode,
                "detail": "detailed",
                "custom_prompt": "",
                "transcript": spoken,
                "transcript_source": "manual captions",
                "transcript_text": spoken,
                "text": spoken if mode == "raw" else "The gist.",
                "markdown": "",
            }

        for mode, expect_commit in (("summary", True), ("raw", False)):
            with tempfile.TemporaryDirectory() as folder:
                issue = {
                    "number": 7,
                    "title": self.URL,
                    "body": drain.build_issue_body("", mode),
                    "labels": [],
                }
                with mock.patch.object(
                    pipeline, "summarize_video", side_effect=fake_summarize_video
                ):
                    outcome = drain.process_issue(
                        FakeGitHub(), issue, False, output_dir=folder
                    )
                transcript = Path(outcome["transcript_path"])
                self.assertTrue(transcript.is_file(), mode)
                self.assertEqual(transcript.parent.name, drain.TRANSCRIPT_DIR)
                self.assertIn("line one", transcript.read_text(encoding="utf-8"))
                # A raw job commits nothing and writes no summary file, so the
                # repo and the summary list both stay honest.
                self.assertEqual(bool(commits), expect_commit, mode)
                self.assertEqual(bool(outcome["local_path"]), expect_commit, mode)
                commits.clear()

    def test_a_transcript_comment_cannot_exceed_what_github_accepts(self):
        bounded = drain.bounded_comment("word " * 40_000)
        self.assertLessEqual(len(bounded), 65_536)
        self.assertIn("Truncated here", bounded)
        self.assertEqual(drain.bounded_comment("short"), "short")  # fits, untouched


class WebTranscriptTests(unittest.TestCase):
    """The web page has to offer the raw option and show what it produced."""

    @classmethod
    def setUpClass(cls):
        import dashboard
        from fastapi.testclient import TestClient

        cls.folder = Path(tempfile.mkdtemp())
        transcripts = cls.folder / drain.TRANSCRIPT_DIR
        transcripts.mkdir()
        blank = chr(10) + chr(10)
        (cls.folder / "a-talk--abc123.txt").write_text(
            "A Talk" + blank + "The gist.", encoding="utf-8"
        )
        (transcripts / "a-talk--abc123.txt").write_text(
            "A Talk" + blank + "every word of it", encoding="utf-8"
        )
        (transcripts / "raw-only--def456.txt").write_text(
            "Raw Only" + blank + "unsummarized words", encoding="utf-8"
        )

        class _StubGitHub:
            def __init__(self):
                self.created = []

            def create_issue(self, title, body=None):
                self.created.append((title, body))
                return {"number": len(self.created)}

            def open_issues(self):
                return []

        class _StubWorker:
            poll_seconds = 120

            def __init__(self):
                self.gh = _StubGitHub()

            def snapshot(self):
                return {
                    "state": "idle",
                    "last_poll": None,
                    "last_result": "",
                    "ok_total": 0,
                    "failed_total": 0,
                }

            def request_drain(self):
                pass

        cls.dashboard = dashboard
        cls.worker = _StubWorker()
        dashboard.attach_worker(cls.worker, summary_dir=str(cls.folder))
        cls.client = TestClient(dashboard.app)

    def _queue(self, **data):
        data.setdefault("url", "https://www.youtube.com/watch?v=abc123")
        self.client.post("/queue", data=data, follow_redirects=False)
        return self.worker.gh.created[-1][1]

    def test_the_page_offers_both_outputs_with_summary_preselected(self):
        page = self.client.get("/").text
        self.assertIn('value="summary" checked', page)
        self.assertIn('value="raw"', page)
        self.assertIn("Transcript only", page)

    def test_asking_for_a_transcript_queues_a_transcript_job(self):
        self.assertEqual(drain.parse_issue_mode(self._queue(mode="raw")), "raw")

    def test_the_default_and_a_tampered_mode_both_summarize(self):
        self.assertEqual(drain.parse_issue_mode(self._queue()), "summary")
        self.assertEqual(drain.parse_issue_mode(self._queue(mode="../etc")), "summary")

    def test_transcripts_are_listed_and_readable(self):
        page = self.client.get("/").text
        self.assertIn("/t/a-talk--abc123", page)
        self.assertIn("raw only", page.lower())  # badge on a transcript-only job
        self.assertIn("every word of it", self.client.get("/t/a-talk--abc123").text)

    def test_a_summary_links_to_the_transcript_it_was_written_from(self):
        page = self.client.get("/s/a-talk--abc123").text
        self.assertIn("/t/a-talk--abc123", page)
        self.assertIn("The gist.", page)

    def test_search_reaches_words_that_only_exist_in_the_transcript(self):
        self.assertIn(
            "/t/raw-only--def456", self.client.get("/search?q=unsummarized").text
        )

    def test_a_transcript_page_cannot_be_talked_out_of_its_folder(self):
        for path in ("/t/..%2F..%2Fsecret", "/t/nothing-here", "/t/a.b"):
            self.assertIn(self.client.get(path).status_code, (400, 404), path)


if __name__ == "__main__":
    unittest.main()

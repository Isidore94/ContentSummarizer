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
        canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        accepted = (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=track",
            "https://youtu.be/dQw4w9WgXcQ?t=42",
            "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        )
        for url in accepted:
            self.assertEqual(pipeline.validate_youtube_url(url), canonical)

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


class IssueOptionsTests(unittest.TestCase):
    def test_detail_only(self):
        self.assertEqual(
            drain.parse_issue_options("detail: complex"),
            {"prompt": "", "detail": "complex"},
        )

    def test_prompt_only(self):
        self.assertEqual(
            drain.parse_issue_options("Just the numbers."),
            {"prompt": "Just the numbers.", "detail": None},
        )

    def test_detail_plus_plain_prompt_case_and_whitespace_insensitive(self):
        opts = drain.parse_issue_options("  LEVEL :  Detailed  \nFocus on risks.")
        self.assertEqual(opts, {"prompt": "Focus on risks.", "detail": "detailed"})

    def test_detail_plus_marker_fenced_prompt_round_trips(self):
        body = drain.build_issue_body("List every ticker.", detail="simple")
        self.assertTrue(body.startswith("detail: simple\n"))
        self.assertEqual(
            drain.parse_issue_options(body),
            {"prompt": "List every ticker.", "detail": "simple"},
        )
        self.assertEqual(drain.parse_issue_prompt(body), "List every ticker.")

    def test_invalid_detail_is_ignored_and_stripped(self):
        with self.assertLogs("drain", level="WARNING"):
            opts = drain.parse_issue_options("detail: huge\nBe brief.")
        self.assertEqual(opts, {"prompt": "Be brief.", "detail": None})

    def test_prose_starting_with_detail_word_stays_in_prompt(self):
        opts = drain.parse_issue_options("Detail: focus on the numbers")
        self.assertEqual(opts["prompt"], "Detail: focus on the numbers")


def _issue(number=1, body=None, labels=()):
    return {
        "number": number,
        "title": "https://www.youtube.com/watch?v=abc123DEF45",
        "body": body,
        "labels": [{"name": n} for n in labels],
    }


class ProcessIssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gh = mock.Mock()
        patcher = mock.patch.multiple(
            drain.pipeline,
            validate_youtube_url=mock.Mock(side_effect=lambda u: u),
            get_metadata=mock.Mock(return_value={"id": "abc123DEF45", "title": "T"}),
            summarize_video=mock.Mock(
                return_value={"id": "abc123DEF45", "title": "T", "text": "fresh"}
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_issue(self, issue, **kw):
        return drain.process_issue(
            self.gh, issue, False, output_dir=self.tmp.name, **kw
        )

    def test_existing_summary_is_reused_without_llm_call(self):
        self.gh.file_exists.return_value = True
        self.gh.file_content.return_value = "old text"
        out = self.run_issue(_issue())
        drain.pipeline.summarize_video.assert_not_called()
        self.gh.commit_file.assert_not_called()
        self.assertIn("reusing existing summary", self.gh.comment.call_args[0][1])
        self.assertIn("old text", self.gh.comment.call_args[0][1])
        self.gh.close_issue.assert_called_once_with(1)
        self.assertTrue(out["reused"])
        self.assertEqual(Path(out["local_path"]).read_text(encoding="utf-8"), "old text")

    def test_custom_prompt_or_label_forces_a_rerun(self):
        self.gh.file_exists.return_value = True
        self.run_issue(_issue(body="Only the numbers."))
        self.run_issue(_issue(labels=[drain.RESUMMARIZE_LABEL]))
        self.assertEqual(drain.pipeline.summarize_video.call_count, 2)

    def test_absent_summary_is_generated_and_issue_detail_wins(self):
        self.gh.file_exists.return_value = False
        self.run_issue(_issue(body="detail: complex"), detail="simple")
        self.assertEqual(
            drain.pipeline.summarize_video.call_args.kwargs["detail"], "complex"
        )
        self.gh.commit_file.assert_called_once()
        self.gh.close_issue.assert_called_once_with(1)


class FileExistsTests(unittest.TestCase):
    def _gh(self, status):
        gh = drain.GitHub("t", "o/r")
        resp = mock.Mock(status_code=status, ok=status < 400, reason="x", text="boom")
        resp.json.side_effect = ValueError
        gh.session = mock.Mock(get=mock.Mock(return_value=resp))
        return gh

    def test_404_is_absent_200_is_present_other_errors_raise(self):
        self.assertFalse(self._gh(404).file_exists("summaries/a.txt"))
        self.assertTrue(self._gh(200).file_exists("summaries/a.txt"))
        with self.assertRaises(RuntimeError):
            self._gh(500).file_exists("summaries/a.txt")


class FailureHandlingTests(unittest.TestCase):
    def setUp(self):
        self.gh = mock.Mock()
        env = mock.patch.dict(os.environ, {"MAX_FAILED_RETRIES": "3"})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def _failures(n):
        return [{"body": "⚠️ Summarization failed; will retry later.\n\n```\nx\n```"}] * n

    def test_is_permanent_failure(self):
        for msg in (
            "ERROR: [youtube] x: Private video. Sign in",
            "Video unavailable",
            "This video has been removed by the uploader",
            "This video is not available",
            "Join this channel to get access: members-only content",
            "Sign in to confirm your age",
            "only YouTube URLs are accepted",
            "invalid YouTube URL",
        ):
            self.assertTrue(drain.is_permanent_failure(RuntimeError(msg)), msg)
        for msg in ("HTTP Error 429", "Connection reset", "OpenAI 503"):
            self.assertFalse(drain.is_permanent_failure(RuntimeError(msg)), msg)

    def test_transient_failure_under_cap_only_labels_failed(self):
        self.gh.list_comments.return_value = self._failures(1)
        self.assertFalse(drain.mark_failed(self.gh, _issue(), RuntimeError("429")))
        self.gh.add_label.assert_called_once_with(1, drain.SKIP_LABEL)
        self.assertEqual(self.gh.comment.call_count, 1)

    def test_cap_reached_gives_up_with_final_comment(self):
        self.gh.list_comments.return_value = self._failures(2)  # this is the 3rd
        self.assertTrue(drain.mark_failed(self.gh, _issue(), RuntimeError("429")))
        labels = [c.args[1] for c in self.gh.add_label.call_args_list]
        self.assertIn(drain.GAVE_UP_LABEL, labels)
        self.gh.ensure_label.assert_called_once()
        final = self.gh.comment.call_args_list[-1].args[1]
        self.assertIn("will not be retried automatically", final)
        self.assertIn(drain.GAVE_UP_LABEL, final)

    def test_permanent_failure_gives_up_immediately(self):
        self.assertTrue(
            drain.mark_failed(self.gh, _issue(), RuntimeError("Private video"))
        )
        self.gh.list_comments.assert_not_called()
        self.assertIn(
            drain.GAVE_UP_LABEL, [c.args[1] for c in self.gh.add_label.call_args_list]
        )
        self.assertIn("permanent", self.gh.comment.call_args_list[-1].args[1])

    def test_give_up_comment_resets_the_count(self):
        comments = self._failures(3) + [
            {"body": "🛑 Giving up. This issue will not be retried automatically."}
        ] + self._failures(1)
        self.assertEqual(drain.count_failures(comments), 1)

    def test_worker_skips_gave_up_issues(self):
        import worker

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPO": "o/r"}):
            w = worker.Worker()
        self.assertFalse(w._due(_issue(labels=[drain.GAVE_UP_LABEL])))
        self.assertTrue(w._due(_issue()))

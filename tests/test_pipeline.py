"""Tests for pipeline.py: URL handling, metadata, VTT timestamps, summary header."""

import subprocess
import unittest
from unittest import mock

import pipeline

VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"


class UrlTests(unittest.TestCase):
    def test_accepted_forms_reduce_to_canonical(self):
        forms = [
            f"https://www.youtube.com/watch?v={VID}",
            f"https://youtube.com/watch?v={VID}&list=PLabc&index=3",
            f"https://www.youtube.com/watch?feature=share&v={VID}&t=42s",
            f"https://m.youtube.com/watch?v={VID}",
            f"https://music.youtube.com/watch?v={VID}&si=abc",
            f"https://youtu.be/{VID}",
            f"https://youtu.be/{VID}?si=trk&t=10",
            f"http://youtu.be/{VID}/",
            f"https://www.youtube.com/shorts/{VID}?feature=share",
            f"https://www.youtube.com/live/{VID}?si=x",
            f"https://www.youtube.com/embed/{VID}",
            f"https://www.youtube-nocookie.com/embed/{VID}",
            f"  https://www.youtube.com/watch?v={VID}  ",
        ]
        for url in forms:
            with self.subTest(url=url):
                self.assertEqual(pipeline.extract_video_id(url), VID)
                self.assertEqual(pipeline.canonical_youtube_url(url), CANON)
                self.assertEqual(pipeline.validate_youtube_url(url), CANON)

    def test_id_with_dash_and_underscore(self):
        self.assertEqual(
            pipeline.canonical_youtube_url("https://youtu.be/a-b_c-d_e-1"),
            "https://www.youtube.com/watch?v=a-b_c-d_e-1",
        )

    def test_no_video_id(self):
        for url in [
            "https://www.youtube.com/",
            "https://www.youtube.com/@somechannel",
            "https://www.youtube.com/channel/UCabc",
            "https://www.youtube.com/playlist?list=PLabc",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/watch?v=short",
            f"https://www.youtube.com/watch?v={VID}extra",
            "https://youtu.be/",
            "https://www.youtube.com/shorts/",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(pipeline.extract_video_id(url))
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.validate_youtube_url(url)

    def test_host_and_scheme_checks_kept(self):
        for url in [
            f"https://evil.com/watch?v={VID}",
            f"https://youtube.com.evil.com/watch?v={VID}",
            f"ftp://www.youtube.com/watch?v={VID}",
            f"www.youtube.com/watch?v={VID}",
            "",
            None,
        ]:
            with self.subTest(url=url):
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.validate_youtube_url(url)
        self.assertIsNone(pipeline.extract_video_id(f"https://evil.com/watch?v={VID}"))
        self.assertIsNone(pipeline.extract_video_id(None))


def _completed(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


class NoPlaylistTests(unittest.TestCase):
    def _cmd_of(self, func, *args):
        with mock.patch.object(
            pipeline.subprocess, "run", return_value=_completed(f"{VID}\tT\tC\t10\t20240101")
        ) as run, mock.patch.object(pipeline.os, "listdir", return_value=["a.webm"]):
            func(*args)
        return run.call_args_list[0].args[0]

    def test_flag_in_every_invocation(self):
        url = f"https://www.youtube.com/watch?v={VID}&list=PLabc"
        self.assertIn("--no-playlist", self._cmd_of(pipeline.get_metadata, url))
        self.assertIn(
            "--no-playlist", self._cmd_of(pipeline._download_subs, CANON, "/tmp", False)
        )
        self.assertIn("--no-playlist", self._cmd_of(pipeline._download_audio, CANON, "/tmp"))

    def test_metadata_uses_canonical_url(self):
        cmd = self._cmd_of(
            pipeline.get_metadata, f"https://youtu.be/{VID}?si=x&list=PLabc"
        )
        self.assertEqual(cmd[-1], CANON)


class MetadataTests(unittest.TestCase):
    def _meta(self, line):
        with mock.patch.object(pipeline, "_run_yt_dlp", return_value=_completed(line)) as r:
            meta = pipeline.get_metadata(CANON)
        template = r.call_args.args[0][r.call_args.args[0].index("--print") + 1]
        self.assertEqual(
            template,
            "%(id)s\t%(title)s\t%(channel)s\t%(duration)s\t%(upload_date)s",
        )
        return meta

    def test_full(self):
        meta = self._meta(f"{VID}\tMy Title\tSome Channel\t3725\t20240131\n")
        self.assertEqual(
            meta,
            {
                "id": VID,
                "title": "My Title",
                "channel": "Some Channel",
                "duration": 3725,
                "upload_date": "20240131",
            },
        )

    def test_na_fields(self):
        meta = self._meta(f"{VID}\tTitle\tNA\tNA\tNA")
        self.assertIsNone(meta["channel"])
        self.assertIsNone(meta["duration"])
        self.assertIsNone(meta["upload_date"])

    def test_empty_fields_and_float_duration(self):
        meta = self._meta(f"{VID}\tTitle\t\t61.5\t")
        self.assertIsNone(meta["channel"])
        self.assertEqual(meta["duration"], 61)
        self.assertIsNone(meta["upload_date"])

    def test_tab_in_title_and_blank_title(self):
        self.assertEqual(self._meta(f"{VID}\ta\tb\tCh\t5\t20240101")["title"], "a\tb")
        self.assertEqual(self._meta(f"{VID}\t\tCh\t5\t20240101")["title"], VID)

    def test_legacy_two_field_output(self):
        meta = self._meta(f"{VID}\tTitle")
        self.assertEqual(meta["title"], "Title")
        self.assertIsNone(meta["channel"])

    def test_format_duration(self):
        self.assertEqual(pipeline.format_duration(0), "0:00")
        self.assertEqual(pipeline.format_duration(65), "1:05")
        self.assertEqual(pipeline.format_duration(3600), "1:00:00")
        self.assertEqual(pipeline.format_duration(3725), "1:02:05")


VTT = """WEBVTT
Kind: captions
Language: en

1
00:00:00.000 --> 00:00:04.000 align:start position:0%
<c.colorE5E5E5>Hello</c> there &amp; welcome

2
00:00:04.000 --> 00:00:08.000
Hello there &amp; welcome
second line

3
00:00:30.500 --> 00:00:35.000
middle of the first minute

4
00:01:02.000 --> 00:01:06.000
<00:01:02.500><c>about a minute in</c>

5
00:01:40.000 --> 00:01:44.000
still the second minute

6
00:02:05.000 --> 00:02:09.000
two minutes in
"""


class StripVttTests(unittest.TestCase):
    def test_markers_roughly_once_a_minute(self):
        out = pipeline.strip_vtt(VTT).splitlines()
        self.assertEqual(
            out,
            [
                "[0:00] Hello there & welcome",
                "second line",
                "middle of the first minute",
                "[1:02] about a minute in",
                "still the second minute",
                "[2:05] two minutes in",
            ],
        )

    def test_no_markup_left(self):
        out = pipeline.strip_vtt(VTT)
        for junk in ("<", ">", "-->", "WEBVTT", "Kind:", "&amp;"):
            self.assertNotIn(junk, out)

    def test_consecutive_duplicates_collapse_across_markers(self):
        raw = (
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nsame\n\n"
            "00:01:30.000 --> 00:01:32.000\nsame\n\n"
            "00:00:40.000 --> 00:00:42.000\nnext\n"
        )
        self.assertEqual(pipeline.strip_vtt(raw), "[0:00] same\nnext")

    def test_hour_format_and_comma_decimals(self):
        raw = "WEBVTT\n\n01:02:03,500 --> 01:02:05,000\nlate\n"
        self.assertEqual(pipeline.strip_vtt(raw), "[1:02:03] late")

    def test_mmss_cue_times_without_hours(self):
        raw = "WEBVTT\n\n00:05.000 --> 00:08.000\nshort form\n"
        self.assertEqual(pipeline.strip_vtt(raw), "[0:05] short form")

    def test_empty(self):
        self.assertEqual(pipeline.strip_vtt("WEBVTT\n"), "")

    def test_whisper_transcripts_have_no_markers(self):
        seg = mock.Mock(text=" hello ")
        model = mock.Mock()
        model.transcribe.return_value = ([seg, seg], None)
        with mock.patch.object(pipeline, "_download_audio", return_value="a.webm"), \
                mock.patch.object(pipeline, "_get_whisper_model", return_value=model):
            self.assertEqual(pipeline.transcribe_local(CANON), "hello hello")


class SummaryPromptTests(unittest.TestCase):
    def test_system_mentions_timestamps(self):
        self.assertIn("[m:ss]", pipeline._SUMMARY_SYSTEM)
        self.assertIn("never invent", pipeline._SUMMARY_SYSTEM)

    def test_channel_line_before_transcript(self):
        prompt = pipeline._summary_prompt("body", "simple", "", "Some  Channel")
        self.assertIn("Speaker/channel: Some Channel\n", prompt)
        self.assertLess(
            prompt.index("Speaker/channel:"), prompt.index("TRANSCRIPT START")
        )

    def test_no_channel_line_by_default(self):
        self.assertNotIn("Speaker/channel", pipeline._summary_prompt("body", "simple"))
        self.assertNotIn(
            "Speaker/channel", pipeline._summary_prompt("body", "simple", "", None)
        )

    def test_channel_threaded_to_provider(self):
        with mock.patch.dict(pipeline.os.environ, {"SUMMARY_PROVIDER": "openai"}), \
                mock.patch.object(pipeline, "_summarize_openai", return_value="x") as m:
            pipeline.summarize("t", detail="simple", channel="Ch")
        self.assertEqual(m.call_args.args[-1], "Ch")
        with mock.patch.dict(pipeline.os.environ, {"SUMMARY_PROVIDER": "anthropic"}), \
                mock.patch.object(pipeline, "_summarize_anthropic", return_value="x") as m:
            pipeline.summarize("t", detail="simple", channel="Ch")
        self.assertEqual(m.call_args.args[-1], "Ch")


class SummarizeVideoHeaderTests(unittest.TestCase):
    def _run(self, meta, custom_prompt=None):
        with mock.patch.object(pipeline, "get_metadata", return_value=meta), \
                mock.patch.object(pipeline, "fetch_captions", return_value="cap"), \
                mock.patch.object(pipeline, "summarize", return_value="BODY") as summ:
            result = pipeline.summarize_video(
                f"https://youtu.be/{VID}?si=x&list=PL", custom_prompt=custom_prompt
            )
        return result, summ

    def test_full_header(self):
        meta = {"id": VID, "title": "T", "channel": "Ch", "duration": 3725,
                "upload_date": "20240131"}
        result, summ = self._run(meta, "focus on X")
        self.assertEqual(
            result["text"],
            f"T\n{CANON}\nChannel: Ch | Duration: 1:02:05 | Uploaded: 2024-01-31\n"
            "Prompt: focus on X\n\nBODY\n",
        )
        self.assertEqual(result["markdown"], result["text"])
        self.assertEqual(summ.call_args.kwargs["channel"], "Ch")

    def test_partial_header(self):
        meta = {"id": VID, "title": "T", "channel": None, "duration": 65,
                "upload_date": None}
        result, _ = self._run(meta)
        self.assertEqual(result["text"], f"T\n{CANON}\nDuration: 1:05\n\nBODY\n")

    def test_header_line_dropped_when_all_missing(self):
        meta = {"id": VID, "title": "T", "channel": None, "duration": None,
                "upload_date": None}
        result, _ = self._run(meta)
        self.assertEqual(result["text"], f"T\n{CANON}\n\nBODY\n")

    def test_old_style_metadata_without_new_keys(self):
        result, _ = self._run({"id": VID, "title": "T"})
        self.assertEqual(result["text"], f"T\n{CANON}\n\nBODY\n")


if __name__ == "__main__":
    unittest.main()

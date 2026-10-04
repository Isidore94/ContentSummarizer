"""Tests that need tkinter / desktop_gui. Skipped when tkinter is unavailable."""
from __future__ import annotations

import importlib.util
import unittest

HAS_TKINTER = importlib.util.find_spec("tkinter") is not None


@unittest.skipUnless(HAS_TKINTER, "tkinter is not available")
class GuiTests(unittest.TestCase):
    UNICORN = (
        "<!DOCTYPE html>\n<!--\n\nHello future GitHubber! I bet you're here to "
        "remove those nasty inline styles,\nDRY up these templates and make 'em "
        "nice and re-usable, right?\n\nPlease, don't.\n\n-->\n<html>\n<head>\n"
        "<title>Unicorn! &middot; GitHub</title>\n<style>body { margin: 0; }</style>\n"
        "</head>\n<body>" + "<p>filler</p>" * 400 + "</body>\n</html>\n"
    )

    def test_gui_labels_never_grow_unbounded(self):
        import desktop_gui

        self.assertLessEqual(len(desktop_gui._one_line(self.UNICORN)), 160)
        self.assertNotIn("\n", desktop_gui._one_line(self.UNICORN))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_TKINTER, "tkinter is not available")
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



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

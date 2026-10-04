"""Tests for worker.py yt-dlp self-update."""

import os
import subprocess
import unittest
from unittest import mock

import worker


def make_worker(hours):
    env = {"GITHUB_TOKEN": "t", "GITHUB_REPO": "o/r"}
    if hours is not None:
        env["YT_DLP_AUTO_UPDATE_HOURS"] = str(hours)
    with mock.patch.dict(os.environ, env), mock.patch.object(worker.drain, "GitHub"):
        if hours is None:
            os.environ.pop("YT_DLP_AUTO_UPDATE_HOURS", None)
        return worker.Worker()


def ok_proc():
    return subprocess.CompletedProcess([], 0, "", "")


class YtDlpUpdateTests(unittest.TestCase):
    def test_disabled_by_default(self):
        w = make_worker(None)
        with mock.patch.object(worker.subprocess, "run") as run:
            self.assertFalse(w.maybe_update_yt_dlp())
        run.assert_not_called()

    def test_runs_when_due_then_waits(self):
        w = make_worker(2)
        with mock.patch.object(worker.subprocess, "run", return_value=ok_proc()) as run, \
                mock.patch.object(worker.time, "time") as now, \
                mock.patch.object(worker.pipeline, "yt_dlp_version", return_value="1"):
            now.return_value = 1000.0
            self.assertTrue(w.maybe_update_yt_dlp())
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[1:], ["-m", "pip", "install", "-U", "--quiet", "yt-dlp"])
            self.assertEqual(run.call_args[1]["timeout"], 300)
            self.assertEqual(w.snapshot()["last_yt_dlp_update"], 1000.0)
            now.return_value = 1000.0 + 3600
            self.assertFalse(w.maybe_update_yt_dlp())
            self.assertEqual(run.call_count, 1)
            now.return_value = 1000.0 + 2 * 3600 + 1
            self.assertTrue(w.maybe_update_yt_dlp())
            self.assertEqual(run.call_count, 2)

    def test_frozen_never_runs(self):
        w = make_worker(1)
        with mock.patch.object(worker.sys, "frozen", True, create=True), \
                mock.patch.object(worker.subprocess, "run") as run:
            self.assertFalse(w.maybe_update_yt_dlp())
        run.assert_not_called()

    def test_failure_never_raises(self):
        w = make_worker(1)
        with mock.patch.object(worker.subprocess, "run", side_effect=OSError("boom")):
            self.assertFalse(w.maybe_update_yt_dlp())
        bad = subprocess.CompletedProcess([], 1, "", "nope")
        w = make_worker(1)
        with mock.patch.object(worker.subprocess, "run", return_value=bad):
            self.assertFalse(w.maybe_update_yt_dlp())
        self.assertIsNone(w.snapshot()["last_yt_dlp_update"])
        w = make_worker(1)
        with mock.patch.object(
            worker.subprocess, "run", side_effect=subprocess.TimeoutExpired("pip", 300)
        ):
            self.assertFalse(w.maybe_update_yt_dlp())


if __name__ == "__main__":
    unittest.main()

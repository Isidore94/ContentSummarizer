import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("GITHUB_TOKEN", "x")
os.environ.setdefault("GITHUB_REPO", "o/r")

from fastapi.testclient import TestClient

import dashboard
import drain


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.worker = mock.MagicMock()
        self.worker.gh = mock.MagicMock()
        self.worker.gh.create_issue.return_value = {"number": 1}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # No `with TestClient` -> lifespan (real Worker/threads) never runs.
        patches = [
            mock.patch.object(dashboard, "worker", self.worker),
            mock.patch.object(dashboard, "_summary_dir_override", self.tmp.name),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(dashboard.app)

    def _queue(self, **data):
        data.setdefault("url", "https://www.youtube.com/watch?v=abc")
        return self.client.post("/queue", data=data, follow_redirects=False)

    def test_queue_without_detail(self):
        r = self._queue(prompt="", detail="default")
        self.assertEqual(r.status_code, 303)
        self.worker.gh.create_issue.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc", body=None
        )

    def test_queue_without_detail_field(self):
        self._queue(prompt="focus on tickers")
        body = self.worker.gh.create_issue.call_args.kwargs["body"]
        self.assertEqual(drain.parse_issue_options(body),
                         {"prompt": "focus on tickers", "detail": None})

    def test_queue_with_detail(self):
        self._queue(prompt="focus on tickers", detail="complex")
        body = self.worker.gh.create_issue.call_args.kwargs["body"]
        self.assertTrue(body.startswith("detail: complex\n"))
        self.assertEqual(drain.parse_issue_options(body),
                         {"prompt": "focus on tickers", "detail": "complex"})

    def test_queue_detail_only_and_bogus(self):
        self._queue(prompt="", detail="simple")
        self.assertEqual(
            self.worker.gh.create_issue.call_args.kwargs["body"], "detail: simple\n"
        )
        self.worker.gh.create_issue.reset_mock()
        self._queue(prompt="", detail="bogus")
        self.assertIsNone(self.worker.gh.create_issue.call_args.kwargs["body"])

    def test_retry_removes_both_labels(self):
        r = self.client.post("/retry/7", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        names = [c.args for c in self.worker.gh.remove_label.call_args_list]
        self.assertEqual(
            names, [(7, drain.SKIP_LABEL), (7, drain.GAVE_UP_LABEL)]
        )
        self.worker.request_drain.assert_called_once()

    def test_retry_survives_label_error(self):
        self.worker.gh.remove_label.side_effect = [RuntimeError("boom"), None]
        r = self.client.post("/retry/7", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.worker.gh.remove_label.call_count, 2)

    def test_summary_meta(self):
        meta = "Channel: Foo | Duration: 10:00 | Uploaded: 2026-01-01"
        self.assertEqual(
            dashboard._summary_meta(f"# Title\nhttps://y.be/x\n{meta}\n\nBody"), meta
        )
        self.assertEqual(dashboard._summary_meta(f"Title\n\n{meta}\nBody"), meta)
        self.assertIsNone(dashboard._summary_meta("# Title\nhttps://y.be/x\n\nBody"))
        self.assertIsNone(dashboard._summary_meta(""))
        self.assertIsNone(dashboard._summary_meta(None))

    def test_home_and_summary_page_show_meta(self):
        meta = "Channel: Foo | Duration: 10:00 | Uploaded: 2026-01-01"
        with open(os.path.join(self.tmp.name, "new--a.txt"), "w", encoding="utf-8") as f:
            f.write(f"# New one\nhttps://y.be/a\n{meta}\n\nBody")
        with open(os.path.join(self.tmp.name, "old--b.txt"), "w", encoding="utf-8") as f:
            f.write("# Old one\nhttps://y.be/b\n\nBody")
        self.worker.snapshot.return_value = {
            "state": "idle", "last_poll": None, "last_result": None,
            "ok_total": 0, "failed_total": 0,
        }
        self.worker.poll_seconds = 60
        self.worker.gh.open_issues.return_value = [
            {"number": 3, "title": "t", "html_url": "http://x",
             "labels": [{"name": drain.GAVE_UP_LABEL}]}
        ]
        home = self.client.get("/").text
        self.assertIn(meta, home)
        self.assertIn("Old one", home)
        self.assertIn("gave up", home)
        self.assertIn('name="detail"', home)
        self.assertIn(meta, self.client.get("/s/new--a").text)
        self.assertEqual(self.client.get("/s/old--b").status_code, 200)
        self.assertIn(meta, self.client.get("/search?q=body").text)


if __name__ == "__main__":
    unittest.main()

"""worker.py — always-on queue drainer (mini-PC mode).

Polls the GitHub Issues queue every POLL_INTERVAL_SECONDS and processes videos
as they arrive, instead of waiting for the daily Task Scheduler run. Failures
get the `summarize-failed` label and are skipped until RETRY_FAILED_HOURS have
passed (or until the label is removed, e.g. with the dashboard's Retry
button).

Run standalone with `python worker.py`, or let dashboard.py host it alongside
the web UI.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

import drain
from app_config import normalize_detail

log = logging.getLogger("worker")

POLL_INTERVAL_DEFAULT = 120
RETRY_FAILED_HOURS_DEFAULT = 24


def _updated_at(issue):
    return datetime.strptime(issue["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


class Worker:
    """Drain loop with shared status, wakeable from the dashboard."""

    def __init__(self, *, summary_detail=None, output_dir=None, enabled=True):
        self.poll_seconds = int(
            os.environ.get("POLL_INTERVAL_SECONDS") or POLL_INTERVAL_DEFAULT
        )
        self.retry_hours = float(
            os.environ.get("RETRY_FAILED_HOURS") or RETRY_FAILED_HOURS_DEFAULT
        )
        self.force_whisper = os.environ.get("FORCE_WHISPER", "").lower() in {
            "1",
            "true",
            "yes",
        }
        self.gh = drain.GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
        self.wake = threading.Event()
        self._stop = threading.Event()
        self._enabled = threading.Event()
        if enabled:
            self._enabled.set()
        self._lock = threading.Lock()
        self._label_ready = False
        self.summary_detail = normalize_detail(
            summary_detail or os.environ.get("SUMMARY_DETAIL", "simple")
        )
        self.output_dir = output_dir or os.environ.get("SUMMARY_FOLDER")
        self.status = {
            "state": "starting",
            "last_poll": None,
            "last_result": "",
            "ok_total": 0,
            "failed_total": 0,
            "listening": enabled,
            "summary_detail": self.summary_detail,
            "output_dir": self.output_dir or "",
        }

    def snapshot(self):
        with self._lock:
            return dict(self.status)

    def _set(self, **kv):
        with self._lock:
            self.status.update(kv)

    def request_drain(self):
        """Wake the loop immediately (dashboard 'Drain now' / new queue item)."""
        self.wake.set()

    def configure(self, *, summary_detail=None, output_dir=None):
        with self._lock:
            if summary_detail is not None:
                self.summary_detail = normalize_detail(summary_detail)
                self.status["summary_detail"] = self.summary_detail
            if output_dir is not None:
                self.output_dir = str(output_dir)
                self.status["output_dir"] = self.output_dir

    def resume(self):
        self._enabled.set()
        self._set(listening=True, state="starting")
        self.wake.set()

    def pause(self):
        """Pause future polls. The current video, if any, finishes safely."""
        self._enabled.clear()
        self._set(listening=False)
        self.wake.set()

    def stop(self):
        self._stop.set()
        self.wake.set()

    def _due(self, issue):
        labels = {l["name"] for l in issue.get("labels", [])}
        if drain.SKIP_LABEL not in labels:
            return True
        age_hours = (
            datetime.now(timezone.utc) - _updated_at(issue)
        ).total_seconds() / 3600
        return age_hours >= self.retry_hours

    def run_once(self):
        """Drain everything currently due. Returns (ok, failed)."""
        if not self._label_ready:
            try:
                self.gh.ensure_label(
                    drain.SKIP_LABEL,
                    description="ContentSummarizer failed on this video",
                )
                self._label_ready = True
            except Exception:
                log.exception("could not ensure the %s label exists", drain.SKIP_LABEL)

        issues = [i for i in self.gh.open_issues() if self._due(i)]
        ok = failed = 0
        for issue in issues:
            self._set(state=f"processing #{issue['number']}")
            try:
                with self._lock:
                    detail = self.summary_detail
                    output_dir = self.output_dir
                drain.process_issue(
                    self.gh,
                    issue,
                    self.force_whisper,
                    detail=detail,
                    output_dir=output_dir,
                )
                ok += 1
            except Exception as exc:
                failed += 1
                log.exception("issue #%s failed", issue["number"])
                try:
                    self.gh.comment(
                        issue["number"],
                        "⚠️ Summarization failed; will retry later.\n\n"
                        f"```\n{drain.failure_message(exc)}\n```",
                    )
                    self.gh.add_label(issue["number"], drain.SKIP_LABEL)
                except Exception:
                    log.exception("could not mark #%s as failed", issue["number"])
        if ok:
            drain.sync_repo()
        return ok, failed

    def run_forever(self):
        log.info(
            "worker started: polling every %ds, retrying failures after %gh",
            self.poll_seconds,
            self.retry_hours,
        )
        while not self._stop.is_set():
            if not self._enabled.is_set():
                self._set(state="paused", listening=False)
                self.wake.wait(1)
                self.wake.clear()
                continue
            self._set(state="draining")
            try:
                ok, failed = self.run_once()
                result = f"{ok} ok, {failed} failed" if ok or failed else "queue empty"
            except Exception as exc:
                log.exception("poll failed")
                ok = failed = 0
                result = f"poll error: {exc}"
            with self._lock:
                self.status["state"] = "idle"
                self.status["last_poll"] = time.time()
                self.status["last_result"] = result
                self.status["ok_total"] += ok
                self.status["failed_total"] += failed
                self.status["listening"] = True
            if result != "queue empty":
                log.info("poll done: %s", result)
            self.wake.wait(self.poll_seconds)
            self.wake.clear()
        self._set(state="stopped", listening=False)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    for var in ("GITHUB_TOKEN", "GITHUB_REPO"):
        if not os.environ.get(var):
            sys.exit(f"Missing required environment variable: {var}")
    Worker().run_forever()


if __name__ == "__main__":
    main()

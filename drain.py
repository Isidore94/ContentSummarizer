"""drain.py — Drain the GitHub Issues queue and summarize each video.

Run by Windows Task Scheduler. Lists open issues in the configured repo (each
issue TITLE is a YouTube URL), runs the summarization pipeline on each, commits
the resulting markdown to summaries/, pastes the summary as a closing comment,
and closes the issue. A video that fails is logged and left open (so it isn't
lost) with the error posted as a comment, and the batch continues.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import sys

import requests
from dotenv import load_dotenv

import pipeline

log = logging.getLogger("drain")

GITHUB_API = "https://api.github.com"
SUMMARY_DIR = "summaries"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# Label put on issues that failed to process. The always-on worker skips
# labeled issues until its retry window elapses (or the label is removed via
# the dashboard's Retry button); a manual `python drain.py` retries them all.
SKIP_LABEL = "summarize-failed"


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


class GitHub:
    """Minimal GitHub REST client scoped to a single repo."""

    def __init__(self, token, repo):
        self.repo = repo  # "owner/name"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _url(self, path):
        return f"{GITHUB_API}/repos/{self.repo}/{path}"

    def _request(self, method, path, **kwargs):
        resp = self.session.request(method, self._url(path), timeout=30, **kwargs)
        if not resp.ok:
            raise RuntimeError(
                f"GitHub {method} {path} failed ({resp.status_code}): {resp.text}"
            )
        return resp

    def open_issues(self):
        """Return open issues (excluding pull requests), oldest first."""
        issues = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                "issues",
                params={
                    "state": "open",
                    "per_page": 100,
                    "page": page,
                    "sort": "created",
                    "direction": "asc",
                },
            ).json()
            if not batch:
                break
            # The issues endpoint also returns PRs; filter them out.
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
            page += 1
        return issues

    def create_issue(self, title):
        return self._request("POST", "issues", json={"title": title}).json()

    def comment(self, number, body):
        self._request("POST", f"issues/{number}/comments", json={"body": body})

    def ensure_label(self, name, color="d93f0b", description=""):
        """Create the label in the repo if it doesn't exist yet."""
        resp = self.session.get(self._url(f"labels/{name}"), timeout=30)
        if resp.ok:
            return
        self._request(
            "POST",
            "labels",
            json={"name": name, "color": color, "description": description},
        )

    def add_label(self, number, name):
        self._request("POST", f"issues/{number}/labels", json={"labels": [name]})

    def remove_label(self, number, name):
        resp = self.session.delete(
            self._url(f"issues/{number}/labels/{name}"), timeout=30
        )
        if not resp.ok and resp.status_code != 404:  # 404 = label already gone
            raise RuntimeError(
                f"GitHub DELETE label {name} failed ({resp.status_code}): {resp.text}"
            )

    def close_issue(self, number):
        self._request(
            "PATCH",
            f"issues/{number}",
            json={"state": "closed", "state_reason": "completed"},
        )

    def commit_file(self, path, content, message):
        """Create or update a file in the repo via the Contents API."""
        # Fetch the existing sha (required to update a file that already exists).
        sha = None
        existing = self.session.get(self._url(f"contents/{path}"), timeout=30)
        if existing.ok:
            sha = existing.json().get("sha")

        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        self._request("PUT", f"contents/{path}", json=payload)


_SLUG_STRIP = re.compile(r"[^\w\s-]")
_SLUG_SPACE = re.compile(r"[\s_-]+")


def sanitize_title(title):
    """Turn a video title into a safe, readable filename stem."""
    slug = _SLUG_STRIP.sub("", title)
    slug = _SLUG_SPACE.sub("-", slug).strip("-").lower()
    return slug[:80].strip("-") or "summary"


def sync_repo():
    """Fast-forward the local clone so summaries committed via the API land on
    disk too (the dashboard reads them from the working tree)."""
    try:
        proc = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode:
            log.warning("git pull failed: %s", (proc.stderr or proc.stdout).strip())
    except Exception as exc:
        log.warning("git pull failed: %s", exc)


def process_issue(gh, issue, force_whisper):
    """Summarize one issue's video, commit it, comment, and close the issue."""
    number = issue["number"]
    url = issue["title"].strip()
    log.info("issue #%s: %s", number, url)

    result = pipeline.summarize_video(url, force_whisper=force_whisper)
    path = f"{SUMMARY_DIR}/{sanitize_title(result['title'])}.md"

    gh.commit_file(path, result["markdown"], f"Add summary: {result['title']}")
    gh.comment(number, result["markdown"])
    gh.close_issue(number)
    if any(l["name"] == SKIP_LABEL for l in issue.get("labels", [])):
        gh.remove_label(number, SKIP_LABEL)  # succeeded on retry
    log.info("issue #%s done -> %s", number, path)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    token = _require_env("GITHUB_TOKEN")
    repo = _require_env("GITHUB_REPO")
    provider = os.environ.get("SUMMARY_PROVIDER", "openai").strip().lower()
    # consumed by pipeline via the environment
    _require_env("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY")
    force_whisper = os.environ.get("FORCE_WHISPER", "").lower() in {"1", "true", "yes"}

    gh = GitHub(token, repo)
    try:
        gh.ensure_label(SKIP_LABEL, description="ContentSummarizer failed on this video")
    except Exception:
        log.exception("could not ensure the %s label exists", SKIP_LABEL)
    issues = gh.open_issues()
    log.info("%d open issue(s) to drain", len(issues))

    failures = 0
    for issue in issues:
        try:
            process_issue(gh, issue, force_whisper)
        except Exception as exc:  # keep the batch going; don't lose the issue
            failures += 1
            log.exception("issue #%s failed", issue["number"])
            try:
                gh.comment(
                    issue["number"],
                    "⚠️ Summarization failed; leaving this issue open.\n\n"
                    f"```\n{exc}\n```",
                )
                gh.add_label(issue["number"], SKIP_LABEL)
            except Exception:
                log.exception("could not mark #%s as failed", issue["number"])

    if len(issues) - failures > 0:
        sync_repo()
    log.info("done: %d ok, %d failed", len(issues) - failures, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

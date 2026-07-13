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
import sys

import requests
from dotenv import load_dotenv

import pipeline

log = logging.getLogger("drain")

GITHUB_API = "https://api.github.com"
SUMMARY_DIR = "summaries"


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

    def comment(self, number, body):
        self._request("POST", f"issues/{number}/comments", json={"body": body})

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
    log.info("issue #%s done -> %s", number, path)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    token = _require_env("GITHUB_TOKEN")
    repo = _require_env("GITHUB_REPO")
    _require_env("ANTHROPIC_API_KEY")  # consumed by pipeline via the environment
    force_whisper = os.environ.get("FORCE_WHISPER", "").lower() in {"1", "true", "yes"}

    gh = GitHub(token, repo)
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
            except Exception:
                log.exception("could not post error comment on #%s", issue["number"])

    log.info("done: %d ok, %d failed", len(issues) - failures, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

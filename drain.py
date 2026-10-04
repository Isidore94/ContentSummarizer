"""drain.py — Drain the GitHub Issues queue and summarize each video.

Run by Windows Task Scheduler. Lists open issues in the configured repo (each
issue TITLE is a YouTube URL), runs the pipeline on each, commits the resulting
plain text to summaries/, pastes it as a closing comment, and closes the issue.
A video that fails is logged and left open (so it isn't lost) with the error
posted as a comment, and the batch continues.

Every job — summarized or not — also writes the full transcript it worked from
to a transcripts/ folder beside the summaries. That file never leaves the
machine: it is the raw material, kept so a thin or wrong summary can be checked
against what was actually said.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

import pipeline
from app_config import DETAIL_LEVELS, executable_dir, normalize_detail

log = logging.getLogger("drain")

GITHUB_API = "https://api.github.com"
SUMMARY_DIR = "summaries"
# Raw transcripts live in a subfolder of wherever summaries are saved, so
# choosing a Google Drive folder carries the logs along with the summaries and
# there is never a second path to configure.
TRANSCRIPT_DIR = "transcripts"
REPO_DIR = str(executable_dir())

# GitHub rejects an issue comment over 65,536 characters. A summary never comes
# close; a raw transcript routinely does, so everything we post is bounded here
# rather than discovered as a 422 halfway through a batch.
MAX_COMMENT_CHARS = 60_000

# Label put on issues that failed to process. The always-on worker skips
# labeled issues until its retry window elapses (or the label is removed via
# the dashboard's Retry button); a manual `python drain.py` retries them all.
SKIP_LABEL = "summarize-failed"

# Terminal state: the drainer stopped retrying (retry cap reached, or the error
# can never succeed, e.g. a private video). Neither the worker nor a manual
# drain touches these; removing the label re-queues the issue.
GAVE_UP_LABEL = "summarize-gave-up"

# A deliberate re-run: with this label the issue is summarized again even when
# a summary for the video is already in the repo.
RESUMMARIZE_LABEL = "resummarize"

# Failures per issue before giving up (env MAX_FAILED_RETRIES overrides).
MAX_RETRIES = 3

# Comment markers. The drainer counts its own failure comments to enforce the
# retry cap, so these strings are load-bearing: change them and old failures
# stop being counted.
FAILURE_MARKER = "Summarization failed"
GAVE_UP_MARKER = "will not be retried automatically"


def failure_message(exc, limit=1500):
    """Bound error text before posting it to an issue comment."""
    text = " ".join(str(exc).strip().splitlines()).replace("```", "'''")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or type(exc).__name__


def bounded_comment(text, limit=MAX_COMMENT_CHARS):
    """Trim a comment to what GitHub will accept, saying so where it cuts."""
    text = text or ""
    if len(text) <= limit:
        return text
    keep = limit - 200
    return (
        text[:keep].rstrip()
        + f"\n\n---\n\n_Truncated here: {keep:,} of {len(text):,} characters. "
        "The complete text is saved on the machine that ran this job._\n"
    )


def response_error(resp, limit=300):
    """Condense a failed GitHub response into one short, displayable line.

    During an outage GitHub answers with a full HTML page (its "Unicorn!"
    screen), not JSON. Putting that whole document in the exception is what
    flooded the status line and the activity log, so keep only the gist.
    """
    detail = ""
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        # Normal API errors are JSON: {"message": "...", "documentation_url": ...}
        detail = str(payload.get("message") or "").strip()
    if not detail:
        text = (resp.text or "").strip()
        if re.match(r"\s*(<!doctype html|<html)", text[:200], re.IGNORECASE):
            detail = "GitHub returned an HTML error page (service outage)"
        else:
            detail = " ".join(text.split())
    if len(detail) > limit:
        detail = detail[: limit - 1] + "…"
    return detail or resp.reason or "no detail"


def max_retries():
    """Retry cap from MAX_FAILED_RETRIES, falling back to MAX_RETRIES."""
    try:
        value = int(os.environ.get("MAX_FAILED_RETRIES") or MAX_RETRIES)
    except ValueError:
        return MAX_RETRIES
    return max(1, value)


# Error text that no amount of retrying fixes. Matched case-insensitively
# against the exception message (yt-dlp wraps these in its own prefixes).
_PERMANENT_PATTERNS = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "this video has been removed",
    "this video is not available",
    "members-only",
    "members only",
    "sign in to confirm your age",
    "only youtube urls are accepted",
    "invalid youtube url",
)


def is_permanent_failure(exc):
    """True when retrying cannot help (private/removed video, bad URL...).

    Transient problems (network, rate limits, LLM outages) return False so
    they keep the normal retry-with-cap behaviour.
    """
    text = str(exc).lower()
    return any(pattern in text for pattern in _PERMANENT_PATTERNS)


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
                f"GitHub {method} {path} failed ({resp.status_code}): "
                f"{response_error(resp)}"
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

    def create_issue(self, title, body=None):
        payload = {"title": title}
        if body:
            payload["body"] = body
        return self._request("POST", "issues", json=payload).json()

    def comment(self, number, body):
        self._request("POST", f"issues/{number}/comments", json={"body": body})

    def list_comments(self, number):
        """Return every comment on an issue, oldest first."""
        comments = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"issues/{number}/comments",
                params={"per_page": 100, "page": page},
            ).json()
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return comments

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
                f"GitHub DELETE label {name} failed ({resp.status_code}): "
                f"{response_error(resp)}"
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

    def file_exists(self, path):
        """True when the repo has a file at `path`; False on 404.

        Any other error (auth, outage) raises: guessing "absent" there would
        make the drainer pay for a duplicate summary.
        """
        resp = self.session.get(self._url(f"contents/{path}"), timeout=30)
        if resp.status_code == 404:
            return False
        if not resp.ok:
            raise RuntimeError(
                f"GitHub GET contents/{path} failed ({resp.status_code}): "
                f"{response_error(resp)}"
            )
        return True

    def files_in_directory(self, path):
        """List repository files in a directory through the Contents API."""
        return self._request("GET", f"contents/{path}").json()

    def file_content(self, path):
        """Return a repository file as decoded UTF-8 text."""
        payload = self._request("GET", f"contents/{path}").json()
        encoded = (payload.get("content") or "").replace("\n", "")
        return base64.b64decode(encoded).decode("utf-8")


# A per-video AI instruction travels to the drainer in the issue BODY (the title
# is always just the URL). The fence keeps the prompt intact and readable on
# github.com; a body with no marker at all is taken as the prompt verbatim, so a
# prompt typed straight into the iOS Shortcut or the GitHub UI still works.
PROMPT_MARKER = "<!-- content-summarizer:prompt -->"
_PROMPT_RE = re.compile(
    # The fence grows past three backticks when the prompt itself contains a
    # code fence, so match its exact width and require the same width to close.
    re.escape(PROMPT_MARKER) + r"\s*(`{3,})(?:\w+)?[ \t]*\n(.*?)\n?\1",
    re.DOTALL,
)


# Optional first line of the body picking this video's detail level, e.g.
# "detail: complex". Lets the iOS Shortcut choose a level without a prompt.
_DETAIL_LINE_RE = re.compile(
    r"^[ \t]*(?:detail|level)[ \t]*:[ \t]*(\S+)[ \t]*(?:\r?\n|$)", re.IGNORECASE
)

# The output mode rides along in the same body: "summary" (the default, so an
# unmarked issue behaves exactly as it always has) or "raw", which skips the
# model and delivers the transcript itself.
MODE_MARKER = "<!-- content-summarizer:mode -->"
MODE_HEADER = "**Transcript only — no AI summary.**"
_MODE_RE = re.compile(re.escape(MODE_MARKER) + r"[ \t]*([A-Za-z-]+)")


def build_issue_body(prompt, mode="summary", *, detail=None):
    """Render detail line, mode block and any custom prompt into an issue body.

    Returns None when there is nothing worth saying: the plain
    summarize-this-video case, which is still the common one.
    """
    mode = pipeline.normalize_output_mode(mode)
    detail = (detail or "").strip().lower()
    if detail and detail not in DETAIL_LEVELS:
        raise ValueError(f"unknown detail level: {detail!r}")
    blocks = []
    if detail:
        blocks.append(f"detail: {detail}\n")
    if mode != "summary":
        blocks.append(f"{MODE_HEADER}\n\n{MODE_MARKER} {mode}\n")
    prompt = (prompt or "").strip()
    if prompt:
        # A fenced block means backticks/markdown in the prompt can't break parsing.
        fence = "`" * max(
            3, max((len(m) for m in re.findall(r"`+", prompt)), default=0) + 1
        )
        blocks.append(
            "**Custom prompt for this summary**\n\n"
            f"{PROMPT_MARKER}\n{fence}text\n{prompt}\n{fence}\n"
        )
    return "\n".join(blocks) or None


def parse_issue_mode(body):
    """The output mode an issue asks for; "summary" unless it clearly says raw."""
    match = _MODE_RE.search(body or "")
    if not match:
        return "summary"
    try:
        return pipeline.normalize_output_mode(match.group(1))
    except pipeline.PipelineError:
        log.warning("issue body asked for unknown mode %r; summarizing", match.group(1))
        return "summary"


def _without_mode_block(text):
    """Drop the mode marker and its heading, so neither can pose as a prompt."""
    return _MODE_RE.sub("", text).replace(MODE_HEADER, "")


def parse_issue_options(body):
    """Split an issue body into {"prompt", "detail", "mode"}.

    A leading `detail:`/`level:` line sets the detail level and is stripped
    from the prompt, as is the mode block. An invalid level is ignored (and
    logged) rather than failing the video; the line is still removed so it
    can't leak into the prompt.
    """
    text = (body or "").strip()
    detail = None
    match = _DETAIL_LINE_RE.match(text)
    if match:
        value = match.group(1).lower()
        if value in DETAIL_LEVELS:
            detail = value
        else:
            log.warning(
                "ignoring invalid detail level %r in issue body (use %s)",
                match.group(1),
                "/".join(DETAIL_LEVELS),
            )
        text = text[match.end():].strip()
    mode = parse_issue_mode(text)
    text = _without_mode_block(text).strip()
    return {"prompt": _extract_prompt(text), "detail": detail, "mode": mode}


def _extract_prompt(text):
    if not text:
        return ""
    match = _PROMPT_RE.search(text)
    if match:
        return match.group(2).strip()
    if PROMPT_MARKER in text:
        # Marked, but the fence was mangled (hand-edited on github.com).
        return text.split(PROMPT_MARKER, 1)[1].strip().strip("`").strip()
    return text


def parse_issue_prompt(body):
    """Extract the custom prompt from an issue body; '' when there is none."""
    return parse_issue_options(body)["prompt"]


_SLUG_STRIP = re.compile(r"[^\w\s-]")
_SLUG_SPACE = re.compile(r"[\s_-]+")


def sanitize_title(title):
    """Turn a video title into a safe, readable filename stem."""
    slug = _SLUG_STRIP.sub("", title)
    slug = _SLUG_SPACE.sub("-", slug).strip("-").lower()
    return slug[:80].strip("-") or "summary"


def summary_filename(title, video_id=None, issue_number=None):
    """Return a readable, collision-resistant .txt filename."""
    identity = re.sub(r"[^A-Za-z0-9_-]", "", video_id or "")
    if not identity and issue_number is not None:
        identity = f"issue-{issue_number}"
    suffix = f"--{identity}" if identity else ""
    return f"{sanitize_title(title)}{suffix}.txt"


def write_summary(output_dir, filename, content):
    """Atomically save a summary in the user-selected local/Drive folder."""
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target


def import_repository_summaries(gh, output_dir):
    """Copy existing repo summaries to a local/Drive folder as .txt files."""
    imported = 0
    for item in gh.files_in_directory(SUMMARY_DIR):
        name = item.get("name", "")
        if item.get("type") != "file" or not name.lower().endswith((".md", ".txt")):
            continue
        filename = f"{Path(name).stem}.txt"
        if (Path(output_dir).expanduser() / filename).exists():
            continue
        content = gh.file_content(item["path"])
        write_summary(output_dir, filename, content)
        imported += 1
    return imported


def sync_repo():
    """Fast-forward the local clone so summaries committed via the API land on
    disk too (the dashboard reads them from the working tree)."""
    if os.environ.get("GITHUB_ACTIONS") or not os.path.isdir(
        os.path.join(REPO_DIR, ".git")
    ):
        return  # cloud runner: nothing local to sync, and HEAD is detached
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


def transcript_dir(output_dir):
    """Where raw transcripts are kept: a subfolder of the summary folder."""
    return Path(output_dir).expanduser() / TRANSCRIPT_DIR


def _reuse_existing_summary(gh, issue, repo_path, filename, destination):
    """Close an issue with a summary already in the repo; no LLM call."""
    number = issue["number"]
    text = gh.file_content(repo_path)
    local_path = Path(destination).expanduser() / filename
    if not local_path.exists():
        write_summary(destination, filename, text)
    gh.comment(
        number, bounded_comment(f"(reusing existing summary for this video)\n\n{text}")
    )
    gh.close_issue(number)
    return text, local_path


def process_issue(gh, issue, force_whisper, *, detail=None, output_dir=None):
    """Run one issue's video through the pipeline, publish it, and close it."""
    number = issue["number"]
    url = pipeline.validate_youtube_url(issue["title"])
    options = parse_issue_options(issue.get("body"))
    custom_prompt = options["prompt"]
    mode = options["mode"]
    labels = {l["name"] for l in issue.get("labels", [])}
    log.info(
        "issue #%s [%s]: %s%s",
        number,
        mode,
        url,
        " (custom prompt)" if custom_prompt else "",
    )

    # The issue's own detail line wins over the worker/global default.
    detail = normalize_detail(
        options["detail"] or detail or os.environ.get("SUMMARY_DETAIL", "detailed")
    )
    destination = output_dir or os.environ.get("SUMMARY_FOLDER") or os.path.join(
        REPO_DIR, SUMMARY_DIR
    )

    # Idempotency: the same video queued twice, or a previous pass that
    # committed the file but failed to comment/close, must not pay for a
    # second LLM summary. A custom prompt, an explicit detail line, raw mode,
    # or the resummarize label means the user wants something different, so
    # those skip the check.
    # NOTE: get_metadata is called again inside summarize_video (an extra
    # yt-dlp round trip). Pass the metadata through once pipeline allows it.
    rerun = (
        bool(custom_prompt)
        or options["detail"] is not None
        or mode != "summary"
        or RESUMMARIZE_LABEL in labels
    )
    if not rerun:
        meta = pipeline.get_metadata(url)
        existing_name = summary_filename(meta["title"], meta.get("id"), number)
        existing_path = f"{SUMMARY_DIR}/{existing_name}"
        if gh.file_exists(existing_path):
            log.info(
                "issue #%s: summary %s already exists; reusing it, no LLM call",
                number,
                existing_path,
            )
            text, local_path = _reuse_existing_summary(
                gh, issue, existing_path, existing_name, destination
            )
            _clear_failure_labels(gh, number, labels)
            return {
                "result": {"id": meta.get("id"), "title": meta["title"], "text": text},
                "mode": mode,
                "repo_path": existing_path,
                "local_path": str(local_path),
                "transcript_path": None,
                "reused": True,
            }

    result = pipeline.summarize_video(
        url,
        force_whisper=force_whisper,
        detail=detail,
        custom_prompt=custom_prompt,
        mode=mode,
    )
    filename = summary_filename(result["title"], result.get("id"), number)

    # Written for every job, whichever output was asked for: this is the whole
    # text the summary came from, and it stays on this machine. Same filename as
    # the summary, so a summary and its source are one folder apart.
    transcript_path = write_summary(
        transcript_dir(destination), filename, result["transcript_text"]
    )

    local_path = None
    repo_path = None
    if mode == "summary":
        local_path = write_summary(destination, filename, result["text"])
        repo_path = f"{SUMMARY_DIR}/{filename}"
        gh.commit_file(
            repo_path,
            result["text"],
            f"Add {detail} summary: {result['title']}",
        )
        gh.comment(number, bounded_comment(result["text"]))
    else:
        # Raw transcripts are not committed to the repo — they are bulk source
        # text, kept locally on purpose. The comment carries as much as GitHub
        # will take so the requester still gets something back on their phone.
        gh.comment(
            number,
            bounded_comment(
                f"📄 **Transcript only** — no summary was generated.\n\n"
                f"{result['text']}"
            ),
        )
    gh.close_issue(number)
    _clear_failure_labels(gh, number, labels)
    log.info("issue #%s done -> %s", number, local_path or transcript_path)
    return {
        "result": result,
        "mode": mode,
        "repo_path": repo_path,
        "local_path": str(local_path) if local_path else None,
        "transcript_path": str(transcript_path),
    }


def _clear_failure_labels(gh, number, labels):
    """Drop failure labels from an issue that has now succeeded."""
    for name in (SKIP_LABEL, GAVE_UP_LABEL):
        if name in labels:
            gh.remove_label(number, name)


def count_failures(comments):
    """Count the drainer's failure comments since the last give-up.

    A give-up comment resets the count, so removing the gave-up label gives
    the issue a fresh set of retries instead of giving up on the first miss.
    """
    count = 0
    for comment in comments:
        body = comment.get("body") or ""
        if GAVE_UP_MARKER in body:
            count = 0
        elif FAILURE_MARKER in body.split("\n", 1)[0]:
            count += 1
    return count


def mark_failed(gh, issue, exc):
    """Record a failed video on its issue; returns True if the drainer gave up.

    Transient failures: comment + `summarize-failed` (retried later). The
    retry cap or a permanent error (private/removed video, bad URL): comment,
    then `summarize-gave-up`, which the worker and drain skip until a human
    removes the label.
    """
    number = issue["number"]
    permanent = is_permanent_failure(exc)
    prior = 0
    if not permanent:
        # Count before commenting so this failure is added exactly once.
        try:
            prior = count_failures(gh.list_comments(number))
        except Exception:
            log.exception("could not read comments on #%s for the retry cap", number)
    attempts = prior + 1
    cap = max_retries()
    give_up = permanent or attempts >= cap

    outcome = (
        "this error is permanent, so it will not be retried."
        if permanent
        else "giving up after repeated failures."
        if give_up
        else "will retry later."
    )
    gh.comment(
        number,
        f"⚠️ {FAILURE_MARKER}; {outcome}\n\n```\n{failure_message(exc)}\n```",
    )
    gh.add_label(number, SKIP_LABEL)
    if give_up:
        gh.ensure_label(
            GAVE_UP_LABEL,
            color="6e7781",
            description="ContentSummarizer stopped retrying this video",
        )
        gh.add_label(number, GAVE_UP_LABEL)
        why = (
            "the error looks permanent (the video can't be fetched)"
            if permanent
            else f"it failed {attempts} times (limit {cap})"
        )
        gh.comment(
            number,
            f"🛑 Giving up: {why}. This issue {GAVE_UP_MARKER}. "
            f"Remove the `{GAVE_UP_LABEL}` label to re-queue it.",
        )
        log.warning("issue #%s marked %s: %s", number, GAVE_UP_LABEL, why)
    return give_up


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
    issues = [
        i
        for i in gh.open_issues()
        if not any(l["name"] == GAVE_UP_LABEL for l in i.get("labels", []))
    ]
    log.info("%d open issue(s) to drain", len(issues))

    failures = 0
    for issue in issues:
        try:
            process_issue(gh, issue, force_whisper)
        except Exception as exc:  # keep the batch going; don't lose the issue
            failures += 1
            log.exception("issue #%s failed", issue["number"])
            try:
                mark_failed(gh, issue, exc)
            except Exception:
                log.exception("could not mark #%s as failed", issue["number"])

    if len(issues) - failures > 0:
        sync_repo()
    log.info("done: %d ok, %d failed", len(issues) - failures, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

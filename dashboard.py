"""dashboard.py — LAN web dashboard + always-on worker (mini-PC mode).

Runs the polling worker (worker.py) in a background thread and serves a small
web UI on the local network: see the queue, paste a URL to queue a video,
drain now, retry failures, and browse/search summaries.

Start with `python dashboard.py`. Configure with DASHBOARD_HOST /
DASHBOARD_PORT in .env (defaults 0.0.0.0:8787). Logs go to worker.log next to
this file (plus the console when one is attached).
"""

from __future__ import annotations

import glob
import html
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import markdown as md
from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

import drain
import pipeline
from worker import Worker

log = logging.getLogger("dashboard")

load_dotenv()

SUMMARY_DIR = os.path.join(drain.REPO_DIR, "summaries")
_STEM_RE = re.compile(r"^[-\w]+$")

# When the desktop app hosts this UI it owns the Worker and the summary folder,
# so we serve its objects instead of building a second (queue-racing) worker.
_external_worker = False
_summary_dir_override: str | None = None

# Viewer mode: run the dashboard WITHOUT its polling worker, so it coexists
# with another drainer (e.g. a self-hosted GitHub Actions runner) instead of
# double-processing the queue. The UI still lists the queue, adds videos, and
# browses summaries — the other drainer does the summarizing.
NO_WORKER = os.environ.get("DASHBOARD_NO_WORKER", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Keep the local summaries/ folder in sync with the repo so the reader always
# shows the latest — summaries are committed via the GitHub API, so a plain
# `git pull` brings them to disk. (No-op when there's nothing to fetch.)
SUMMARY_PULL_SECONDS = int(os.environ.get("SUMMARY_PULL_SECONDS") or 60)

# Auto-refresh: how often the home page may reload itself, in seconds (0 = never).
# Per-device rather than a server setting, because the phone left open on the counter
# and the PC someone is typing into want different answers.
REFRESH_CHOICES = (0, 15, 30, 60)
REFRESH_COOKIE = "cs_refresh"
REFRESH_COOKIE_MAX_AGE = 400 * 24 * 3600  # remember the choice for well over a year

worker: Worker | None = None


# The GPU badge is decoration, but probing a switched-off GPU PC costs a real timeout
# (~3s here, measured), and every home() render paid it. Auto-refresh would charge that
# every few seconds, making the page feel broken. Cached for display only — the routing
# decision in pipeline.transcribe() still probes live, because sending a job to a node
# that died 20 seconds ago is a different kind of wrong.
GPU_STATUS_TTL = 30
_gpu_status = {"checked_at": 0.0, "online": False}


def _gpu_online_cached():
    now = time.time()
    if now - _gpu_status["checked_at"] > GPU_STATUS_TTL:
        _gpu_status["online"] = pipeline.gpu_node_online()
        _gpu_status["checked_at"] = now
    return _gpu_status["online"]


def _refresh_seconds(raw):
    """The refresh interval a request asked for, or 0 for off. Never trusts the value."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return value if value in REFRESH_CHOICES else 0


def attach_worker(existing: Worker, summary_dir=None):
    """Host this UI on someone else's Worker (the desktop app embeds it).

    The caller already runs the drain loop and owns the summary folder, so
    lifespan() must not start a second worker.
    """
    global worker, _external_worker, _summary_dir_override
    worker = existing
    _external_worker = True
    if summary_dir:
        _summary_dir_override = str(summary_dir)


def set_summary_dir(path):
    """Point the reader at the folder the desktop app is saving into."""
    global _summary_dir_override
    _summary_dir_override = str(path)


def _summary_dir():
    return _summary_dir_override or SUMMARY_DIR


def _pull_loop():
    while True:
        time.sleep(SUMMARY_PULL_SECONDS)
        drain.sync_repo()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    if _external_worker:
        yield  # the host app owns the worker, the pull loop, and the folder
        return
    worker = Worker()
    drain.sync_repo()  # refresh summaries on startup
    threading.Thread(target=_pull_loop, daemon=True, name="summary-pull").start()
    if NO_WORKER:
        worker._set(state="viewer mode (an external runner drains the queue)")
    else:
        threading.Thread(
            target=worker.run_forever, daemon=True, name="drain-worker"
        ).start()
    yield


app = FastAPI(lifespan=lifespan)


_CSS = """
/* One set of tokens, themed once. Every colour below is a variable so the dark
   theme is a single block at the bottom rather than a second stylesheet. */
:root {
  color-scheme: light dark;
  --bg: #f5f6f8;
  --surface: #ffffff;
  --surface-2: #eef0f4;
  --line: #e2e5ea;
  --ink: #14171c;
  --ink-soft: #565d6b;
  --ink-faint: #858d9b;
  --brand: #2f6fed;
  --brand-ink: #ffffff;
  --brand-soft: #e8f0fe;
  --ok: #0f7a52;
  --ok-soft: #e0f4ec;
  --err: #c0362b;
  --err-soft: #fceceb;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(18, 22, 32, .04), 0 8px 24px rgba(18, 22, 32, .05);
}
* { box-sizing: border-box; }
body {
  /* Segoe UI Variable is Windows 11's text face and is noticeably better fitted
     than plain Segoe UI at body sizes; -apple-system takes over on the phone. */
  font-family: "Segoe UI Variable Text", -apple-system, BlinkMacSystemFont,
               "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.65;
  max-width: 760px;
  margin: 0 auto;
  padding: 36px 20px 72px;
  background: var(--bg);
  color: var(--ink);
  overflow-wrap: break-word;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
h1 { font-size: 27px; line-height: 1.25; letter-spacing: -.021em; font-weight: 640;
     margin: 0 0 28px; }
h1 a { color: inherit; text-decoration: none; }
/* Section labels: small and quiet, but with room above so each block reads as
   its own thing instead of running into the card before it. */
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .085em;
     font-weight: 660; color: var(--ink-faint); margin: 36px 0 12px; }
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
.muted { color: var(--ink-faint); font-size: 13.5px; }

.card { background: var(--surface); border: 1px solid var(--line);
        border-radius: var(--radius); padding: 22px 24px; box-shadow: var(--shadow); }
.card-divider { border: 0; border-top: 1px solid var(--line); margin: 20px -24px; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }

/* Status: a label above its value, in columns that wrap. The old single line ran
   four labelled values together and read as one long sentence. */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
         gap: 18px 26px; }
.stat { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
              font-weight: 660; color: var(--ink-faint); }
.stat-value { font-size: 15px; line-height: 1.45; font-variant-numeric: tabular-nums;
              overflow-wrap: anywhere; }

button, .btn {
  font: inherit; font-size: 14px; font-weight: 550; line-height: 1;
  min-height: 40px; padding: 0 16px; border: 1px solid transparent; border-radius: 10px;
  background: var(--brand); color: var(--brand-ink); cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  text-decoration: none; transition: filter .15s ease, background .15s ease;
}
button:hover, .btn:hover { filter: brightness(1.07); text-decoration: none; }
button:active, .btn:active { filter: brightness(.95); }
:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.btn-quiet { background: var(--surface-2); color: var(--ink-soft); border-color: var(--line); }

input[type=text], input[type=search], textarea {
  width: 100%; font: inherit; font-size: 15px; padding: 11px 14px;
  border: 1px solid var(--line); border-radius: 10px;
  background: var(--surface); color: var(--ink);
}
input[type=text]:focus, input[type=search]:focus, textarea:focus {
  outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft);
}
::placeholder { color: var(--ink-faint); }
textarea { resize: vertical; min-height: 84px; margin-top: 10px; line-height: 1.55; }
.field-hint { margin: 10px 0 16px; }
.searchbar { display: flex; gap: 10px; align-items: center; }
.searchbar input { flex: 1; min-width: 0; }

form.inline { display: inline-flex; margin: 0; }
form.inline button, .btn-sm { min-height: 30px; padding: 0 11px; font-size: 12.5px;
  background: var(--surface-2); color: var(--ink-soft); border-color: var(--line); }

.badge { font-size: 11px; font-weight: 660; text-transform: uppercase;
         letter-spacing: .04em; padding: 3px 9px; border-radius: 999px; line-height: 1.5; }
.badge.fail { background: var(--err-soft); color: var(--err); }
.badge.on { background: var(--ok-soft); color: var(--ok); }
.badge.off { background: var(--surface-2); color: var(--ink-faint); }

.seg { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.seg form { display: inline-flex; }
.seg button { min-height: 34px; padding: 0 13px; font-size: 13px;
              background: var(--surface-2); color: var(--ink-soft); border-color: var(--line); }
.seg button.active { background: var(--brand); color: var(--brand-ink); border-color: var(--brand); }
.seg .btn { min-height: 34px; font-size: 13px; }

/* Rows, not bullets: a title on the left and its date on the right, separated by
   hairlines. Bulleted lines of "title date title date" were the worst of the clutter. */
.list { list-style: none; padding: 0; margin: 0; }
.list li { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
           padding: 13px 0; border-top: 1px solid var(--line); margin: 0; }
.list li:first-child { border-top: 0; padding-top: 2px; }
.list li:last-child { padding-bottom: 2px; }
/* Queue entries are raw YouTube URLs: one unbreakable token long enough to push the
   whole page wider than a phone, which then clips every row on the right. min-width:0
   lets the flex item shrink; anywhere lets the URL itself break. */
.list a { font-weight: 550; min-width: 0; overflow-wrap: anywhere; }
.list .when { margin-left: auto; font-size: 13px; color: var(--ink-faint);
              font-variant-numeric: tabular-nums; white-space: nowrap; }
.list .snippet { flex-basis: 100%; margin: 2px 0 0; font-size: 13.5px;
                 color: var(--ink-faint); }
.empty { color: var(--ink-faint); margin: 2px 0; }

.prose { background: var(--surface); border: 1px solid var(--line);
         border-radius: var(--radius); padding: 30px 34px; box-shadow: var(--shadow); }
.prose h1 { font-size: 22px; margin-bottom: 18px; }
/* A summary is prose, not code: reading it in the body face at a generous measure
   beats the monospace block it used to be. */
.prose pre { font: inherit; line-height: 1.72; white-space: pre-wrap;
             overflow-wrap: anywhere; margin: 0; }
.backlink { margin: 0 0 18px; font-size: 14px; }

@media (max-width: 560px) {
  body { padding: 22px 15px 56px; font-size: 15.5px; }
  h1 { font-size: 23px; margin-bottom: 22px; }
  h2 { margin-top: 28px; }
  .card { padding: 17px 18px; border-radius: 12px; }
  .card-divider { margin: 17px -18px; }
  .prose { padding: 22px 20px; }
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1014;
    --surface: #171a20;
    --surface-2: #232833;
    --line: #2b313c;
    --ink: #e7e9ee;
    --ink-soft: #a7aebc;
    --ink-faint: #7e8695;
    --brand: #5b8cff;
    --brand-ink: #0a0f1c;
    --brand-soft: #1a2337;
    --ok: #3fcb92;
    --ok-soft: #12291f;
    --err: #ff7a6d;
    --err-soft: #2c1715;
    --shadow: none;
  }
}
"""


# Reloading the page under someone mid-sentence would throw away a half-typed URL or
# prompt, so the timer holds while a field is focused or has anything in it, and says so.
# The countdown is not decoration: without it a page that reloads itself looks like a bug.
_REFRESH_JS = """
(function () {
  var every = %d, left = every;
  var out = document.getElementById("refresh-countdown");
  function busy() {
    var el = document.activeElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return true;
    var fields = document.querySelectorAll("input[type=text], textarea");
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].value.trim()) return true;
    }
    return false;
  }
  setInterval(function () {
    if (busy()) {
      left = every;
      if (out) out.textContent = "paused while you type";
      return;
    }
    left -= 1;
    if (left <= 0) { location.reload(); return; }
    if (out) out.textContent = "refreshing in " + left + "s";
  }, 1000);
})();
"""


def _page(title, body, refresh=0):
    # <noscript> carries the plain-HTML fallback: a browser without JS still keeps up,
    # it just cannot pause for typing the way the script does.
    head_extra = (
        f'<noscript><meta http-equiv="refresh" content="{int(refresh)}"></noscript>'
        if refresh
        else ""
    )
    script = f"<script>{_REFRESH_JS % int(refresh)}</script>" if refresh else ""
    return (
        "<!doctype html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"{head_extra}"
        f"<style>{_CSS}</style>"
        "</head><body>"
        '<h1><a href="/">📼 ContentSummarizer</a></h1>'
        f"{body}"
        f"{script}"
        "</body></html>"
    )


def _refresh_controls(refresh):
    """The Refresh-now button and the auto-refresh segmented control."""
    seg = []
    for value in REFRESH_CHOICES:
        label = "Off" if value == 0 else f"{value}s"
        active = " active" if value == refresh else ""
        seg.append(
            '<form method="post" action="/settings/refresh">'
            f'<input type="hidden" name="seconds" value="{value}">'
            f'<button class="{active.strip()}">{label}</button></form>'
        )
    countdown = (
        f' <span class="muted" id="refresh-countdown">refreshing in {refresh}s</span>'
        if refresh
        else ""
    )
    return (
        '<div class="seg" style="margin-top:12px">'
        '<a class="btn btn-quiet" href="/">&#8635; Refresh now</a>'
        '<span class="muted">Auto-refresh:</span>'
        f'{"".join(seg)}{countdown}'
        "</div>"
    )


def _ago(ts):
    if not ts:
        return "never"
    delta = int(time.time() - ts)
    if delta < 90:
        return f"{delta}s ago"
    if delta < 90 * 60:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


def _summary_title(text):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[2:].strip() if line.startswith("# ") else line
    return None


def _body_without_title(text, title):
    """The summary minus its own first-line title, which the page shows as a heading.

    Only strips a line that IS the title, so a summary that opens straight into prose
    keeps every word.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip() == (title or "").strip():
            rest = lines[index + 1 :]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(rest)
        break
    return text


def _summary_files():
    files = glob.glob(os.path.join(_summary_dir(), "*.txt"))
    files.extend(glob.glob(os.path.join(_summary_dir(), "*.md")))
    return sorted(
        files,
        key=os.path.getmtime,
        reverse=True,
    )


@app.get("/", response_class=HTMLResponse)
def home(cs_refresh: str = Cookie(default="0")):
    refresh = _refresh_seconds(cs_refresh)
    s = worker.snapshot()

    queue_err = None
    try:
        issues = worker.gh.open_issues()
    except Exception as exc:
        issues, queue_err = [], str(exc)

    rows = []
    for issue in issues:
        labels = {l["name"] for l in issue.get("labels", [])}
        failed = drain.SKIP_LABEL in labels
        badge = ' <span class="badge fail">failed</span>' if failed else ""
        retry = (
            f'<form class="inline" method="post" action="/retry/{issue["number"]}">'
            "<button>Retry now</button></form>"
            if failed
            else ""
        )
        rows.append(
            f'<li><span class="muted">#{issue["number"]}</span> '
            f'<a href="{html.escape(issue["html_url"])}" target="_blank">'
            f'{html.escape(issue["title"])}</a>{badge}'
            f'<span class="when">{retry}</span></li>'
        )
    if queue_err:
        queue_html = f'<p class="empty">Could not reach GitHub: {html.escape(queue_err)}</p>'
    elif rows:
        queue_html = '<ul class="list">' + "".join(rows) + "</ul>"
    else:
        queue_html = '<p class="empty">Queue is empty.</p>'

    items = []
    for path in _summary_files()[:20]:
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            title = _summary_title(open(path, encoding="utf-8").read()) or stem
        except OSError:
            title = stem
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        items.append(
            f'<li><a href="/s/{stem}">{html.escape(title)}</a>'
            f'<span class="when">{date}</span></li>'
        )
    summaries_html = (
        '<ul class="list">' + "".join(items) + "</ul>"
        if items
        else '<p class="empty">No summaries yet.</p>'
    )

    provider = os.environ.get("SUMMARY_PROVIDER", "openai")
    backend = os.environ.get(
        "TRANSCRIBE_BACKEND", pipeline.TRANSCRIBE_BACKEND_DEFAULT
    ).strip().lower()

    gpu_html = ""
    if pipeline._gpu_node_url():
        online = _gpu_online_cached()
        gpu_badge = (
            '<span class="badge on">online</span>'
            if online
            else '<span class="badge off">offline</span>'
        )
        gpu_html = (
            '<div class="stat"><span class="stat-label">GPU PC</span>'
            f'<span class="stat-value">{gpu_badge}</span></div>'
        )

    if NO_WORKER:
        seg_html = ""
        controls_html = (
            '<p class="muted">Viewer mode — the GitHub Actions runner drains the '
            "queue. Adding a video below opens an issue that the runner picks up.</p>"
        )
        meta_html = f'<p class="muted">summaries via {html.escape(provider)}</p>'
    else:
        seg = []
        for value, label in (("auto", "Auto"), ("openai", "OpenAI API"), ("remote", "GPU PC")):
            active = " active" if backend == value else ""
            seg.append(
                f'<form method="post" action="/settings/transcribe">'
                f'<input type="hidden" name="mode" value="{value}">'
                f'<button class="{active.strip()}">{label}</button></form>'
            )
        seg_html = (
            f'<div class="seg"><span class="muted">Transcription:</span>{"".join(seg)}'
            '<span class="muted">(resets to .env on restart)</span></div>'
        )
        # Drain does real work, so it keeps the filled button; refreshing only
        # re-reads a page and sits below it as a quiet one.
        controls_html = (
            '<div class="row"><form method="post" action="/drain">'
            "<button>Drain now</button></form></div>"
        )
        meta_html = (
            f'<p class="muted">Summaries via {html.escape(provider)} · '
            f"polling every {worker.poll_seconds}s</p>"
        )
    # Both modes get it: viewer mode is exactly when the page cannot know that the
    # external runner has finished something.
    controls_html += _refresh_controls(refresh)

    stats = [
        '<div class="stat"><span class="stat-label">State</span>'
        f'<span class="stat-value">{html.escape(str(s["state"]))}</span></div>'
    ]
    if not NO_WORKER:
        stats.append(
            '<div class="stat"><span class="stat-label">Last poll</span>'
            f'<span class="stat-value">{_ago(s["last_poll"])} '
            f'<span class="muted">{html.escape(s["last_result"] or "—")}</span>'
            "</span></div>"
        )
        stats.append(
            '<div class="stat"><span class="stat-label">Lifetime</span>'
            f'<span class="stat-value">{s["ok_total"]} ok · {s["failed_total"]} failed'
            "</span></div>"
        )
    stats.append(gpu_html)

    body = f"""
<div class="card">
  <div class="stats">{"".join(stats)}</div>
  {meta_html}
  <hr class="card-divider">
  {seg_html}
  {controls_html}
</div>

<h2>Add a video</h2>
<div class="card">
  <form method="post" action="/queue">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." required>
    <textarea name="prompt" rows="3" placeholder="Optional: tell the AI how to summarize this one — e.g. &quot;focus on the investing advice and list every ticker mentioned&quot;"></textarea>
    <p class="muted field-hint">Leave the prompt empty for the normal summary.</p>
    <button>Queue it</button>
  </form>
</div>

<h2>Queue</h2>
<div class="card">{queue_html}</div>

<h2>Summaries</h2>
<div class="card">
  <form class="searchbar" method="get" action="/search">
    <input type="search" name="q" placeholder="Search summaries&hellip;" aria-label="Search summaries">
    <button>Search</button>
  </form>
  <hr class="card-divider">
  {summaries_html}
</div>
"""
    return _page("ContentSummarizer", body, refresh=refresh)


@app.get("/s/{stem}", response_class=HTMLResponse)
def summary_page(stem: str):
    if not _STEM_RE.match(stem):
        return HTMLResponse("Bad name", status_code=400)
    txt_path = os.path.join(_summary_dir(), stem + ".txt")
    md_path = os.path.join(_summary_dir(), stem + ".md")
    path = txt_path if os.path.isfile(txt_path) else md_path
    if not os.path.isfile(path):
        return HTMLResponse(
            _page(
                "Not found",
                '<div class="card"><p class="empty">No such summary.</p></div>'
                '<p class="backlink" style="margin-top:18px">'
                '<a href="/">&larr; Back</a></p>',
            ),
            status_code=404,
        )
    text = open(path, encoding="utf-8").read()
    title = _summary_title(text) or stem
    if path.endswith(".txt"):
        # The first line is the title; showing it as a heading rather than as the first
        # line of body text is most of what makes this read like a document.
        body = (
            f"<h1>{html.escape(title)}</h1>"
            f"<pre>{html.escape(_body_without_title(text, title))}</pre>"
        )
    else:
        # Model output is untrusted; escaping first prevents raw-HTML/script
        # injection while retaining the useful Markdown structure.
        body = md.markdown(html.escape(text), extensions=["extra"])
    return _page(
        title,
        f'<p class="backlink"><a href="/">&larr; Back</a></p>'
        f'<article class="prose">{body}</article>',
    )


@app.get("/search", response_class=HTMLResponse)
def search(q: str = ""):
    q = q.strip()
    results = []
    if q:
        needle = q.lower()
        for path in _summary_files():
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                continue
            if needle in text.lower():
                stem = os.path.splitext(os.path.basename(path))[0]
                line = next(
                    (l for l in text.splitlines() if needle in l.lower()), ""
                )
                results.append((stem, _summary_title(text) or stem, line))

    items = "".join(
        f'<li><a href="/s/{stem}">{html.escape(title)}</a>'
        f'<p class="snippet">{html.escape(line[:180])}</p></li>'
        for stem, title, line in results
    )
    if items:
        count = len(results)
        hits = (
            f'<h2>{count} match{"" if count == 1 else "es"}</h2>'
            f'<div class="card"><ul class="list">{items}</ul></div>'
        )
    elif q:
        hits = '<div class="card"><p class="empty">No matches.</p></div>'
    else:
        hits = ""
    body = f"""
<p class="backlink"><a href="/">&larr; Back</a></p>
<div class="card">
  <form class="searchbar" method="get" action="/search">
    <input type="search" name="q" value="{html.escape(q, quote=True)}"
           placeholder="Search summaries&hellip;" aria-label="Search summaries">
    <button>Search</button>
  </form>
</div>
{hits}
"""
    return _page(f"Search: {q}" if q else "Search", body)


@app.post("/queue")
def queue_video(url: str = Form(...), prompt: str = Form("")):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return HTMLResponse(
            _page(
                "Error",
                '<div class="card"><p class="empty">That doesn&rsquo;t look like a URL.'
                "</p></div>"
                '<p class="backlink" style="margin-top:18px"><a href="/">&larr; Back</a></p>',
            ),
            status_code=400,
        )
    prompt = pipeline.normalize_custom_prompt(prompt)
    worker.gh.create_issue(url, body=drain.build_issue_body(prompt))
    worker.request_drain()
    return RedirectResponse("/", status_code=303)


@app.post("/drain")
def drain_now():
    worker.request_drain()
    return RedirectResponse("/", status_code=303)


@app.post("/settings/transcribe")
def set_transcribe_mode(mode: str = Form(...)):
    mode = mode.strip().lower()
    if mode in {"auto", "openai", "remote"}:
        # pipeline.transcribe() reads the env on every call, and the worker
        # thread shares this process — so this takes effect immediately.
        # It is not persisted; .env wins again after a restart.
        os.environ["TRANSCRIBE_BACKEND"] = mode
        log.info("transcription mode set to %s", mode)
    return RedirectResponse("/", status_code=303)


@app.post("/settings/refresh")
def set_refresh(seconds: str = Form("0")):
    """Remember this browser's auto-refresh choice. Per-device, so the phone on the
    kitchen counter and the PC someone is typing at can differ."""
    value = _refresh_seconds(seconds)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        REFRESH_COOKIE,
        str(value),
        max_age=REFRESH_COOKIE_MAX_AGE,
        samesite="lax",
        httponly=False,
    )
    return response


@app.post("/retry/{number}")
def retry(number: int):
    try:
        worker.gh.remove_label(number, drain.SKIP_LABEL)
    except Exception:
        log.exception("could not remove label from #%s", number)
    worker.request_drain()
    return RedirectResponse("/", status_code=303)


def main():
    for var in ("GITHUB_TOKEN", "GITHUB_REPO"):
        if not os.environ.get(var):
            sys.exit(f"Missing required environment variable: {var}")

    handlers = [
        RotatingFileHandler(
            os.path.join(drain.REPO_DIR, "worker.log"),
            maxBytes=5_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    ]
    if sys.stderr:  # absent under pythonw.exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT") or 8787)

    lan = _lan_ip()
    mode = "viewer (external drainer)" if NO_WORKER else "worker + web UI"
    print(
        "\n  ContentSummarizer server\n"
        f"    mode:  {mode}\n"
        f"    local: http://localhost:{port}\n"
        f"    LAN:   http://{lan}:{port}   (open this on your phone)\n"
        "    Ctrl+C to stop.\n",
        flush=True,
    )
    log.info("server starting on http://%s:%d (%s)", host, port, mode)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_config=None)


def serve_in_thread(host=None, port=None):
    """Serve the web UI on a daemon thread; return (thread, url).

    Used by the desktop app, which has already called attach_worker(). Binding
    0.0.0.0 is what makes the UI reachable from other PCs on the network.
    """
    import socket

    import uvicorn

    host = host or os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(port or os.environ.get("DASHBOARD_PORT") or 8787)

    # Bind here rather than inside the thread: a port clash must surface to the
    # caller as an exception, not die silently on a background thread and leave
    # the UI advertising a URL that answers nothing.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(128)
        sock.set_inheritable(True)
        port = sock.getsockname()[1]  # resolves port 0 to the real one
    except OSError:
        sock.close()
        raise

    config = uvicorn.Config(app, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)
    # uvicorn installs SIGINT/SIGTERM handlers, which only the main thread may
    # do — Tk owns the main thread here, so the app closing stops us instead.
    server.install_signal_handlers = False
    thread = threading.Thread(
        target=lambda: server.run(sockets=[sock]), daemon=True, name="lan-dashboard"
    )
    thread.start()
    return thread, f"http://{_lan_ip()}:{port}"


def _lan_ip():
    """Best-effort local network IP for the startup banner."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    main()

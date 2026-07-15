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
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

import drain
import pipeline
from worker import Worker

log = logging.getLogger("dashboard")

load_dotenv()

SUMMARY_DIR = os.path.join(drain.REPO_DIR, "summaries")
_STEM_RE = re.compile(r"^[-\w]+$")

worker: Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    worker = Worker()
    threading.Thread(
        target=worker.run_forever, daemon=True, name="drain-worker"
    ).start()
    yield


app = FastAPI(lifespan=lifespan)


_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.55 system-ui, "Segoe UI", sans-serif; max-width: 840px;
       margin: 0 auto; padding: 24px 16px; background: #f7f7f8; color: #1a1a1a; }
h1 { font-size: 22px; margin: 0 0 4px; }
h1 a { color: inherit; text-decoration: none; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .04em;
     color: #666; margin: 22px 0 8px; }
.card { background: #fff; border: 1px solid #e3e3e6; border-radius: 10px;
        padding: 14px 18px; margin: 10px 0; }
.muted { color: #777; font-size: 13px; }
input[type=text] { width: 100%; max-width: 520px; padding: 8px 10px;
        border: 1px solid #ccc; border-radius: 8px; background: inherit; color: inherit; }
button { padding: 7px 14px; border: 0; border-radius: 8px; background: #2563eb;
         color: #fff; cursor: pointer; font-size: 14px; }
button:hover { background: #1d4ed8; }
form.inline { display: inline; margin-left: 6px; }
form.inline button { padding: 2px 10px; font-size: 12px; background: #64748b; }
.badge { border-radius: 6px; padding: 1px 8px; font-size: 12px; margin-left: 6px; }
.badge.fail { background: #dc2626; color: #fff; }
.badge.on { background: #16a34a; color: #fff; }
.badge.off { background: #6b7280; color: #fff; }
.seg { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.seg form { display: inline; }
.seg button { background: #64748b; padding: 4px 12px; font-size: 13px; }
.seg button.active { background: #2563eb; }
ul { padding-left: 20px; margin: 6px 0; }
li { margin: 7px 0; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
.prose { background: #fff; border: 1px solid #e3e3e6; border-radius: 10px;
         padding: 20px 26px; }
.prose h1 { font-size: 20px; }
.status-line { display: flex; gap: 18px; flex-wrap: wrap; }
@media (prefers-color-scheme: dark) {
  body { background: #121214; color: #e6e6e9; }
  .card, .prose { background: #1c1c1f; border-color: #333338; }
  h2 { color: #9a9aa3; }
  .muted { color: #94949c; }
  input[type=text] { border-color: #44444a; }
  a { color: #7aa2ff; }
}
"""


def _page(title, body):
    return (
        "<!doctype html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{_CSS}</style>"
        "</head><body>"
        '<h1><a href="/">📼 ContentSummarizer</a></h1>'
        f"{body}"
        "</body></html>"
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
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _summary_files():
    return sorted(
        glob.glob(os.path.join(SUMMARY_DIR, "*.md")),
        key=os.path.getmtime,
        reverse=True,
    )


@app.get("/", response_class=HTMLResponse)
def home():
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
            f'<li>#{issue["number"]} '
            f'<a href="{html.escape(issue["html_url"])}" target="_blank">'
            f'{html.escape(issue["title"])}</a>{badge}{retry}</li>'
        )
    if queue_err:
        queue_html = f'<p class="muted">Could not reach GitHub: {html.escape(queue_err)}</p>'
    elif rows:
        queue_html = "<ul>" + "".join(rows) + "</ul>"
    else:
        queue_html = '<p class="muted">Queue is empty.</p>'

    items = []
    for path in _summary_files()[:20]:
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            title = _summary_title(open(path, encoding="utf-8").read()) or stem
        except OSError:
            title = stem
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        items.append(
            f'<li><a href="/s/{stem}">{html.escape(title)}</a> '
            f'<span class="muted">{date}</span></li>'
        )
    summaries_html = (
        "<ul>" + "".join(items) + "</ul>"
        if items
        else '<p class="muted">No summaries yet.</p>'
    )

    provider = os.environ.get("SUMMARY_PROVIDER", "openai")
    backend = os.environ.get(
        "TRANSCRIBE_BACKEND", pipeline.TRANSCRIBE_BACKEND_DEFAULT
    ).strip().lower()

    gpu_html = ""
    if pipeline._gpu_node_url():
        online = pipeline.gpu_node_online()
        gpu_badge = (
            '<span class="badge on">online</span>'
            if online
            else '<span class="badge off">offline</span>'
        )
        gpu_html = f"<span><b>GPU PC:</b> {gpu_badge}</span>"

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

    body = f"""
<div class="card">
  <div class="status-line">
    <span><b>State:</b> {html.escape(str(s["state"]))}</span>
    <span><b>Last poll:</b> {_ago(s["last_poll"])} ({html.escape(s["last_result"] or "—")})</span>
    <span><b>Lifetime:</b> {s["ok_total"]} ok / {s["failed_total"]} failed</span>
    {gpu_html}
  </div>
  <p class="muted">summaries via {html.escape(provider)} · polling every {worker.poll_seconds}s</p>
  {seg_html}
  <p style="margin-bottom:0"><form class="inline" method="post" action="/drain" style="margin-left:0">
    <button>Drain now</button>
  </form></p>
</div>

<h2>Add a video</h2>
<div class="card">
  <form method="post" action="/queue">
    <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." required>
    <button>Queue it</button>
  </form>
</div>

<h2>Queue</h2>
<div class="card">{queue_html}</div>

<h2>Summaries</h2>
<div class="card">
  <form method="get" action="/search">
    <input type="text" name="q" placeholder="Search summaries...">
    <button>Search</button>
  </form>
  {summaries_html}
</div>
"""
    return _page("ContentSummarizer", body)


@app.get("/s/{stem}", response_class=HTMLResponse)
def summary_page(stem: str):
    if not _STEM_RE.match(stem):
        return HTMLResponse("Bad name", status_code=400)
    path = os.path.join(SUMMARY_DIR, stem + ".md")
    if not os.path.isfile(path):
        return HTMLResponse(
            _page("Not found", '<p>No such summary.</p><p><a href="/">← back</a></p>'),
            status_code=404,
        )
    text = open(path, encoding="utf-8").read()
    body = md.markdown(text, extensions=["extra"])
    return _page(
        _summary_title(text) or stem,
        f'<p><a href="/">← back</a></p><article class="prose">{body}</article>',
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
        f'<div class="muted">{html.escape(line[:180])}</div></li>'
        for stem, title, line in results
    )
    hits = (
        f"<ul>{items}</ul>"
        if items
        else ('<p class="muted">No matches.</p>' if q else "")
    )
    body = f"""
<p><a href="/">← back</a></p>
<div class="card">
  <form method="get" action="/search">
    <input type="text" name="q" value="{html.escape(q, quote=True)}" placeholder="Search summaries...">
    <button>Search</button>
  </form>
</div>
{hits}
"""
    return _page(f"Search: {q}" if q else "Search", body)


@app.post("/queue")
def queue_video(url: str = Form(...)):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return HTMLResponse(
            _page(
                "Error",
                "<p>That doesn't look like a URL.</p>"
                '<p><a href="/">← back</a></p>',
            ),
            status_code=400,
        )
    worker.gh.create_issue(url)
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
    log.info("dashboard starting on http://%s:%d", host, port)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()

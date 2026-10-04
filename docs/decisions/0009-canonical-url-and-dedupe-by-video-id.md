# 0009 — Canonical watch URL; dedupe by video id

Date: 2026-10-04

## Context
Phone shares arrive as `youtu.be`, `/shorts/`, `/live/`, `m.`/`music.` links, or
carry `&list=` and `&si=` params. The same video queued twice (or a pass that
committed the file but failed before closing the issue) paid for a second LLM
summary, and `&list=` links could pull a whole playlist through yt-dlp.

## Decision
`pipeline.canonical_youtube_url` reduces every accepted form to
`https://www.youtube.com/watch?v=<id>`; channel/playlist-only links are
rejected. yt-dlp runs with `--no-playlist`. Before summarizing, `drain.py`
checks whether `summaries/<slug>--<id>.txt` exists in the repo; if so it posts
that text, closes the issue, and makes no LLM call. A custom prompt, an explicit
`detail:` line, or the `resummarize` label skips the check (the user wants a
different summary). Summary headers also gain a Channel/Duration/Uploaded line.

## Rationale
The video id is the only stable identity; the repo's `summaries/` folder is
already the store (0001), so it doubles as the dedupe index with no extra state.
A non-404 error from the existence check raises instead of assuming "absent",
so an outage can't cause duplicate paid summaries.

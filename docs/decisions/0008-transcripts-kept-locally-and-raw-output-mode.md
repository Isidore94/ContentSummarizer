# 0008 — Transcripts kept locally; summary or raw output per video

Date: 2026-08-27

## Context
Until now the transcript existed only in memory for the length of one
summarization call. Nothing on disk recorded what the model had actually been
given, so a summary that read thin or wrong could not be checked against what
was said — the only recourse was re-running the whole video at full cost. There
was also no way to ask for the words themselves: every queued video paid for a
model pass whether or not a summary was the point.

## Decision
`pipeline.summarize_video()` returns the transcript alongside the finished text,
and `drain.process_issue()` writes it to `<summary folder>/transcripts/` under
the same filename as the summary — for every job, in either mode. Transcripts
are gitignored (`summaries/transcripts/`) and never committed.

A video is queued in one of two output modes, carried in the issue body next to
the custom prompt (`drain.MODE_MARKER`, values `summary` / `raw`):

- `summary` — the existing path. It is the default, and an issue with no marker
  takes it, so everything queued before this (and every iOS Shortcut) is
  unaffected.
- `raw` — the transcript is the deliverable. No model call is made at all, and
  nothing is committed to `summaries/`.

Both GUIs offer the choice and read the transcripts back: the desktop window
switches its list between the two folders, the dashboard lists and serves them
at `/t/{stem}` and includes them in search.

Every issue comment now goes through `drain.bounded_comment()`.

## Rationale
The transcript is the evidence behind the summary, and it is worth far more on
disk than the bytes it costs; keeping it under the summary's own filename makes
"show me the source" a lookup rather than a re-run. It stays local because it is
bulk source text — a long video's transcript dwarfs its summary, and the repo is
a store of finished summaries, not a corpus.

Raw mode exists because "what did they actually say" is a real request that a
summarizer answers worse than a transcript does, and answering it should not
cost an API call.

The comment bound is not cosmetic: GitHub rejects comments over 65,536
characters, which a raw transcript routinely exceeds. Without it, the 422 would
have failed a job that had in fact succeeded.

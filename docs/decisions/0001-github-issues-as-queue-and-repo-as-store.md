# 0001 — GitHub Issues as the job queue; the repo itself as the summary store

Date: backfilled 2026-08-01

## Context
Videos are queued from a phone and processed later by whichever machine is on
(desktop, mini-PC, or a runner). The results need to be readable from anywhere.

## Decision
The queue is this repo's GitHub Issues: one open issue per video, title = URL,
optional steering prompt in the body. Completion = summary committed to
`summaries/`, pasted as a closing comment, issue closed. Failures are labeled
`summarize-failed` and left open for retry. The dashboard's reader auto-syncs
summaries from the repo.

## Rationale
RATIONALE UNKNOWN - confirm with Aaron. Strongly implied but never written down:
zero extra infrastructure, one fine-grained PAT covers queue + storage, the iOS
Shortcut only needs a single create-issue POST, and closing comments make results
phone-readable.

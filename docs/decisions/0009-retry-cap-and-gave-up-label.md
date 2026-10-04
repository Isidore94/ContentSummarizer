# 0009 — Retry cap and the `summarize-gave-up` label

Date: 2026-10-04

## Context
Failed issues kept the `summarize-failed` label and were retried forever (every
`RETRY_FAILED_HOURS`, and on every manual drain). Private, removed, members-only
or age-gated videos and non-YouTube URLs can never succeed, so they burned
yt-dlp calls and piled up error comments.

## Decision
After `MAX_FAILED_RETRIES` failures (default 3), or immediately for permanent
errors (pattern-matched in `drain.is_permanent_failure`), the drainer adds
`summarize-gave-up` and a final comment. Both `python drain.py` and the worker
skip these issues; removing the label re-queues the issue. The failure count is
derived from the drainer's own failure comments (reset by a give-up comment), so
no state lives outside GitHub. `summarize-failed` behaviour is unchanged.

## Rationale
Keeps the queue-is-issues model (0001): the label is visible and editable from
the phone, and no database is needed. Cost: the comment marker strings in
`drain.py` are load-bearing; changing them silently resets old counts.

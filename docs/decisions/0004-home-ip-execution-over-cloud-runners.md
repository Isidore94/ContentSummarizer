# 0004 — Run video fetching from a home IP; cloud mode parked

Date: backfilled 2026-08-01

## Context
The most hands-off mode was GitHub Actions on hosted runners (commit 8a411a7,
"Add cloud mode"), with the drain triggered on issue-open.

## Decision
Queue draining runs at home: `serve.py` (current), the desktop exe, a scheduled
`drain.py`, or a self-hosted runner. The hosted-runner workflow survives as
`workflow_dispatch`-only, with its event triggers commented out.

## Rationale
Documented in `.github/workflows/summarize.yml`: YouTube blocks GitHub's
datacenter IPs for many videos ("which is why the GitHub-hosted runner failed"),
so yt-dlp must run from the home IP. History shows the progression: cloud mode
(8a411a7) → self-hosted runner (3cba5cb) → self-contained `serve.py` (2ee4cd0).

## Alternatives rejected
GitHub-hosted runners — kept as a documented but inactive mode needing cookies.

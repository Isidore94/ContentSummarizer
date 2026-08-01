# 0002 — Direct GitHub REST via `requests`, not PyGithub

Date: backfilled 2026-08-01

## Context
The app needs a handful of GitHub operations: list issues, commit a file via the
Contents API, comment, close.

## Decision
Call the GitHub REST API directly with `requests`; no PyGithub dependency.
Owner/repo comes from `GITHUB_REPO` in `.env`, never hardcoded.

## Rationale
Documented in README ("Notes") and `requirements.txt`: "the calls are few and
explicit... and it keeps the dependency surface small."

## Alternatives rejected
PyGithub — explicitly named and rejected in the README for the reason above.

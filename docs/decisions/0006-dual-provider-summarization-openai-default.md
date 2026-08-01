# 0006 — Dual summarization providers; OpenAI is the default

Date: backfilled 2026-08-01

## Context
Summarization started Anthropic-only; commit cde28c3 added "OpenAI as default
summarizer alongside Anthropic".

## Decision
`SUMMARY_PROVIDER` selects `openai` (default, `gpt-4o-mini`) or `anthropic`
(`claude-haiku-4-5`); both models are pinned in `pipeline.py` with the comment
"both fast and cheap". Either API key alone is enough to run the app.

## Rationale
The fast-and-cheap model tier is documented. Why OpenAI became the *default*
over Anthropic is not — RATIONALE UNKNOWN - confirm with Aaron.

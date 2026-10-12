# 0008 — Parakeet over ssh as the fallback for a rate-limited IP

Date: 2026-10-11

## Context
YouTube started answering this IP's subtitle requests with HTTP 429 (Too Many
Requests) while metadata still worked. The 429 raised out of `fetch_captions`, so
videos failed before ever reaching the transcription fallback — and the `openai`
and `local` backends download audio from this same IP anyway.

## Decision
A subtitle 429 now counts as "no captions", so the video falls through to
`transcribe()`. A new `parakeet` backend runs `bin/pete-transcribe <video id>` over
ssh on `PARAKEET_HOST` (default `claude-host`), which fetches and transcribes the
audio on its own IP. `auto` now tries the GPU node, then parakeet (when the ssh
host answers), then the OpenAI audio API. Only the regex-validated 11-char id is
passed to the remote shell. No wake-on-LAN: an asleep host counts as offline.

## Consequences
- Rate-limited videos still get summarized whenever the parakeet host is up.
- The worker needs key-based, non-interactive ssh to `PARAKEET_HOST`.
- Other subtitle errors still fail the video as before.

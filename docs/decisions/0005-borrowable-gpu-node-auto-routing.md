# 0005 — Borrowable GPU node with auto-routed transcription

Date: backfilled 2026-08-01

## Context
The always-on mini-PC has no GPU, but the main desktop (RTX 3080 Ti) does — when
it happens to be powered on.

## Decision
`gpu_node.py` runs a small HTTP transcription server on the CUDA desktop
(optional shared secret `GPU_NODE_TOKEN`). With `TRANSCRIBE_BACKEND=auto` (the
default), the worker borrows that GPU whenever the node is reachable and falls
back to the OpenAI audio API otherwise; `local`/`openai`/`remote` pin a backend.
The dashboard shows node status and can pin the mode.

## Rationale
Evident in README and commit ef7257f ("Add borrowable GPU node with auto-routing
transcription"): free local GPU transcription when available, paid API only when
the desktop is off, and no requirement to keep a power-hungry GPU box always on.

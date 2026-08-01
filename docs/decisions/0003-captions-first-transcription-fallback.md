# 0003 — Captions first, transcription only as fallback

Date: backfilled 2026-08-01

## Context
Most YouTube videos already carry manual or auto captions; audio transcription is
slow, GPU/API-dependent, and costs money.

## Decision
`pipeline.py` fetches existing captions with yt-dlp (`--write-auto-sub
--write-subs --skip-download`, manual subs preferred, VTT stripped to plain
text). Only caption-less videos hit the transcription backend (`faster-whisper`
large-v3 on CUDA locally, the OpenAI audio API, or a remote GPU node).
`FORCE_WHISPER=1` skips captions deliberately.

## Rationale
Evident in README and pipeline comments: captions already exist for most videos;
"Only the whisper fallback needs the GPU; the caption path does not."

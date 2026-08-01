# 0007 — PyInstaller exe with exact pins; `.env` never bundled

Date: backfilled 2026-08-01

## Context
The recommended mini-PC deployment is a standalone Windows executable
(`ContentSummarizer.exe`, built by `build_exe.ps1` from `desktop_gui.py`).

## Decision
The exe build uses `requirements-mini-pc.txt` with exact `==` pins (plus
`requirements-build.txt` for PyInstaller). The exe deliberately does not embed
`.env`, ffmpeg, or the local CUDA stack — `.env` is copied beside it and loaded
at runtime (`app_config.load_runtime_env`), and it supports only the caption +
OpenAI/remote-GPU transcription paths the mini-PC actually uses.

## Rationale
Documented in `requirements-mini-pc.txt` ("Exact versions make rebuilds
repeatable; update deliberately and retest") and README ("The executable
deliberately does not embed `.env`..."): repeatable rebuilds, no credentials
inside a distributable binary, no useless GPU/CUDA weight on a GPU-less box.

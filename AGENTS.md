# ContentSummarizer — AI context index

A personal YouTube-to-summary pipeline. The queue is this repo's own GitHub Issues
(issue title = the YouTube URL), fed from a phone via an iOS Shortcut or the LAN
dashboard. A drainer fetches captions (or transcribes audio), summarizes with
OpenAI/Anthropic, saves plain text to a user-chosen folder (incl. Google Drive),
commits a copy to `summaries/`, posts it as a closing comment, and closes the issue.
A video can instead be queued raw — transcript only, no model call. Either way the
full transcript is kept locally beside the summaries.

## Core loop / data flow
- Ingest: iOS Shortcut (Share Sheet → create-issue API) or dashboard paste box → open GitHub issue. Optional per-video steering prompt in the issue body (`drain.PROMPT_MARKER`, capped at 2,000 chars) and output mode (`drain.MODE_MARKER`: `summary` default / `raw`); an unmarked body summarizes, so pre-existing issues are unaffected.
- Drain (`drain.py`): list open issues → `pipeline.py` per video → always write `<summary folder>/transcripts/<title>--<id>.txt` → if summarizing, also write + commit `summaries/<title>--<id>.txt` → paste the result as a closing comment (via `bounded_comment`) → close issue. Raw jobs commit nothing. On failure: error comment + `summarize-failed` label, issue stays open, auto-retried after `RETRY_FAILED_HOURS` (default 24).
- `pipeline.py`: yt-dlp captions first (manual subs preferred over auto; VTT stripped to text) → caption-less fallback per `TRANSCRIBE_BACKEND` (`auto` = GPU node when reachable else OpenAI audio API; `local` = faster-whisper large-v3 on CUDA; `openai`; `remote`) → in `summary` mode, summarize per `SUMMARY_PROVIDER` (openai default, `gpt-4o`; anthropic alternate, `claude-sonnet-5`) into Simple/Detailed/Complex plain text; in `raw` mode the transcript itself is the output and no provider is called. The result always carries `transcript` + a ready-to-save `transcript_text`.
- Run modes — pick exactly one drainer: `ContentSummarizer.exe` (`desktop_gui.py`; recommended on the mini-PC), `python serve.py` (worker + FastAPI LAN dashboard on `:8787`, hosted `dashboard.py`), scheduled `python drain.py`, or the currently-inactive GitHub Actions workflow (`.github/workflows/summarize.yml`).
- `gpu_node.py`: borrowable transcription server on the CUDA desktop; the mini-PC worker uses it via `GPU_NODE_URL`/`GPU_NODE_TOKEN` whenever that PC is on.
- Config: `.env` next to the exe/source (`app_config.load_runtime_env`); GUI settings persist to `%LOCALAPPDATA%\ContentSummarizer\settings.json`.

## Hard invariants
- One drainer at a time — never run two modes together or issues double-process (workflow also enforces a `summarize-queue` concurrency group).
- Never commit real keys: `.env` is git-ignored; only `.env.example` with empty placeholders is committed. The exe deliberately does not embed `.env`, ffmpeg, or the CUDA stack.
- A failed video never stops the batch: log, comment, label, leave open, move on.
- Video fetching must run from a home IP — YouTube blocks GitHub's datacenter IPs (why the hosted-runner cloud mode is parked; see workflow comments).
- OpenAI audio API caps uploads at ~25 MB: transcode to mono 16 kHz mp3 and chunk anything still over.
- Per-video custom prompts steer, never replace: bounded to 2,000 chars so they can't crowd out the transcript.
- Transcripts stay on the machine that made them: written for every job, never committed (`summaries/transcripts/` is gitignored). They are bulk source text, and the repo stores finished summaries.
- Every issue comment goes through `drain.bounded_comment()` — GitHub rejects comments over 65,536 chars, which a raw transcript routinely exceeds.
- An unrecognized output mode summarizes rather than dropping the video.

## Tech stack + key deps
- Python 3.10+, Windows-first (Task Scheduler, PowerShell, PyInstaller exe).
- `yt-dlp` + ffmpeg — captions/audio; `faster-whisper` — local CUDA transcription (RTX 3080 Ti).
- `openai` + `anthropic` — summarization providers; `requests` — direct GitHub REST calls (chosen over PyGithub; see decisions).
- `fastapi`/`uvicorn`/`markdown` — LAN dashboard; tkinter — desktop GUI; `python-dotenv` — config.
- Requirements layers: `requirements.txt` (full desktop) / `-cloud` (slim, no GPU stack) / `-mini-pc` (exact pins for the exe build) / `-build` (adds pyinstaller).

## Commands
- Test: `python -m unittest tests.test_app` from the repo root (54 tests; needs tkinter, so run on the desktop, not headless Linux).
- Drain once: `python drain.py` (venv activated, `.env` filled).
- Worker + dashboard: `python serve.py` → `http://<pc>:8787`.
- Build the exe: `build_exe.ps1` → `dist/`; deploy per `EXE_SETUP.md`.
- No CI test gate: the only workflow is the (inactive) queue drainer.

## Where to read more
- `README.md` — the full operating guide: all five run modes, `.env` reference, fine-grained PAT scopes, Task Scheduler setup. Read first.
- `EXE_SETUP.md` / `SETUP_MINI_PC.md` / `SETUP_SELF_HOSTED.md` / `SETUP_CLOUD.md` — per-mode deployment guides.
- `MOBILE_SHORTCUT.md` — the iOS Shortcut recipe that posts issues from the phone.
- `docs/decisions/` — backfilled decision records; read before changing library, queue, or deployment choices.

`AGENTS.md` is a copy of this file (symlinks don't survive Windows checkouts) — edit CLAUDE.md, then re-copy.

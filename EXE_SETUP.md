# ContentSummarizer desktop app

## First launch

Before enabling the listener, make the queue repository private so unknown
GitHub users cannot submit work that consumes your API credits.

1. Install ffmpeg once: `winget install Gyan.FFmpeg`.
2. Put `ContentSummarizer.exe` in a permanent folder.
3. Copy `.env.example` beside it as `.env` and fill in your GitHub and API keys.
4. Double-click `ContentSummarizer.exe`.
5. Select a summary folder. A folder managed by Google Drive for desktop is
   supported; new summaries are written there as `.txt` files.
6. Choose Simple, Detailed, or Complex. The choice is saved and applies to the
   next queued video.

The listener starts automatically by default and polls the same GitHub Issues
queue as the Python version. Use **Pause listening** before changing machines so
only one listener is active.

On first start, existing `.md` and `.txt` summaries in the repository are
copied into the selected folder as `.txt`; nothing is deleted from either side.

## Adding a video, with an optional AI prompt

The **Add a video** box takes a YouTube URL plus an optional **AI prompt** that
tailors that one summary — e.g. *"focus on the investing advice and list every
ticker mentioned"*. Leave the prompt empty for the normal Simple/Detailed/Complex
summary. The prompt travels in the queue issue's body and is recorded in the
finished `.txt` on a `Prompt:` line, so a summary always explains its own shape.

## Summary or raw transcript

**Output** picks what the next queued video becomes:

- **AI summary** (the default) — the normal Simple/Detailed/Complex summary.
- **Transcript only** — the AI is skipped entirely. No model call, no API cost,
  no interpretation: you get every word that was said.

The choice resets to *AI summary* after each video is queued, so a one-off raw
job cannot silently become the new default. The AI prompt is ignored for a
transcript-only job. The same two options are on the web GUI, and the iPhone
Shortcut can ask for a raw transcript too (see MOBILE_SHORTCUT.md).

## Transcripts are kept for every video

Whichever output you asked for, the full transcript is saved to a
`transcripts` subfolder of your summary folder, under the same filename as the
summary. It opens with a line saying where the words came from — manual
captions, auto-generated captions, or transcribed audio — and how many there
were.

This is the complete text the summary was written from. Keep it and a summary
that reads thin or wrong can be checked against what was actually said, instead
of being re-run at full cost on a hunch.

- **Show: Summaries / Transcripts** switches the list between the two folders.
- **View transcript** / **View summary** jumps between the two halves of
  whatever is selected.
- Changing your summary folder moves the transcripts with it.
- Transcripts stay on this PC. They are never committed to the repository —
  they are bulk source text, and a long video's transcript is far larger than
  its summary. A transcript-only job reports back on the GitHub issue as a
  comment (truncated if the video is long) and saves the whole thing here.

## Web GUI for other PCs on your network

When the listener starts, the app also serves the web GUI on this PC's network
address — the window shows the link (e.g. `http://192.168.0.223:8787`) with
**Open** and **Copy link** buttons. Any PC, tablet, or phone on the same network
can open that link to queue videos (prompt box and summary/transcript choice
included), watch the queue, and read both the summaries and the transcripts
they were written from. Notes:

- Windows Firewall needs an inbound rule for TCP 8787. If it's missing, run this
  once in an **admin** PowerShell:
  ```powershell
  New-NetFirewallRule -DisplayName ContentSummarizer -Direction Inbound `
    -Protocol TCP -LocalPort 8787 -Action Allow
  ```
- Change the port with `DASHBOARD_PORT` in `.env`. Set `DASHBOARD_HOST=127.0.0.1`
  to keep the UI on this PC only.
- The web GUI and the window share one listener, so a video is never summarized
  twice. Don't also run `serve.py` against the same queue.

Your iPhone Shortcut does not need any of this — it posts straight to GitHub
(see MOBILE_SHORTCUT.md) and works from anywhere, not just your network.

## Files and credentials

- `.env` remains beside the executable and is never embedded in it.
- GUI settings are saved in `%LOCALAPPDATA%\ContentSummarizer\settings.json`.
- Rotating logs are saved in `%LOCALAPPDATA%\ContentSummarizer\ContentSummarizer.log`.
- The executable supports `TRANSCRIBE_BACKEND=auto`, `openai`, or `remote`.
  Local CUDA transcription is intentionally excluded from the mini-PC build.

## Optional start at Windows logon

Press `Win+R`, enter `shell:startup`, and place a shortcut to
`ContentSummarizer.exe` in that folder. Because this is an interactive GUI,
Windows must have a signed-in desktop session for the window to open.

## Rebuilding

From the repository:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.\build_exe.ps1
```

The finished bundle is `dist\ContentSummarizer.exe`.

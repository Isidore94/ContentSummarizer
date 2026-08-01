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

## Web GUI for other PCs on your network

When the listener starts, the app also serves the web GUI on this PC's network
address — the window shows the link (e.g. `http://192.168.0.223:8787`) with
**Open** and **Copy link** buttons. Any PC, tablet, or phone on the same network
can open that link to queue videos (prompt box included), watch the queue, and
read summaries. Notes:

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

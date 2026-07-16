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

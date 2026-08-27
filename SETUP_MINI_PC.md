# Mini PC setup — desktop executable

The recommended setup is now the Windows GUI executable. Build it with
`build_exe.ps1`, then follow **[EXE_SETUP.md](EXE_SETUP.md)**. The app listens to
the same GitHub Issues queue, saves `.txt` summaries in a folder selected from
the GUI (including Google Drive for desktop folders), and supports Simple,
Detailed, and Complex summaries — or no summary at all, if you queue a video as
a transcript-only job. Either way the full transcript is kept in a
`transcripts` subfolder of that same folder, readable from both GUIs.

The source-based worker/dashboard instructions below remain available as a
legacy/headless alternative.

## Legacy source-based worker + dashboard

Goal: the mini PC continuously drains the queue (summaries land ~2 minutes
after you share from your phone) and serves a LAN dashboard, so the main
desktop no longer needs to be on. No GPU required — caption-less videos are
transcribed with the OpenAI audio API.

## 1. Install prerequisites (once)

In a PowerShell window:

```powershell
winget install Git.Git
winget install Python.Python.3.11
winget install Gyan.FFmpeg
```

Close and reopen the terminal afterward so PATH updates take effect.

## 2. Clone the repo and install dependencies

```powershell
git clone https://github.com/Isidore94/ContentSummarizer.git $env:USERPROFILE\ContentSummarizer
cd $env:USERPROFILE\ContentSummarizer
git checkout claude/youtube-summary-pipeline-ylpn2y
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 3. Bring over `.env`

Copy `.env` from the desktop (`d:\ContentSummarizer\.env`) to the repo root
here — move it privately (USB stick, RDP copy/paste); never commit or email
it.

One edit after copying: remove (or blank) the `TRANSCRIBE_BACKEND=local` line.
The mini PC has no CUDA GPU, so it should use the default `auto` backend:
prefer the desktop's GPU node when the desktop is on (`GPU_NODE_URL` is
already in the copied `.env`), otherwise the OpenAI audio API.

## 4. Test drive

```powershell
.venv\Scripts\python.exe drain.py        # one-shot drain, should say "0 open issue(s)" or process the queue
.venv\Scripts\python.exe dashboard.py    # worker + web UI
```

Open http://localhost:8787 — you should see the dashboard. **Allow the Windows
firewall prompt** (private networks) so other devices can reach it. Find the
mini PC's LAN address with `ipconfig` (IPv4 Address), then from your phone
visit `http://<that-ip>:8787`.

## 5. Auto-start at logon

```powershell
$repo = "$env:USERPROFILE\ContentSummarizer"
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\pythonw.exe" `
                                  -Argument "dashboard.py" `
                                  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "ContentSummarizerWorker" `
    -Action $action -Trigger $trigger -Settings $settings
```

Notes:

- `-ExecutionTimeLimit ([TimeSpan]::Zero)` disables the default 3-day kill so
  the worker runs indefinitely.
- `pythonw.exe` runs without a console window; logs go to `worker.log` in the
  repo folder.
- The trigger is **at logon**, so enable automatic sign-in (Settings →
  Accounts, or `netplwiz`) if you want the worker to come back by itself after
  a power cut or reboot.
- Start it immediately without rebooting: `Start-ScheduledTask -TaskName ContentSummarizerWorker`

## 6. Optional: lend the desktop's GPU when it's on

The desktop (MAINPC) can serve its RTX 3080 Ti to the mini PC's worker as a
transcription service. With `TRANSCRIBE_BACKEND=auto` (the mini PC default),
caption-less videos use the desktop GPU for free whenever the desktop is
powered on, and fall back to the OpenAI audio API when it isn't. The
dashboard shows the GPU PC as online/offline and has an Auto / OpenAI API /
GPU PC switch.

On the **desktop**, run the node once interactively to accept the firewall
prompt (choose *private networks*):

```powershell
cd d:\ContentSummarizer
.venv\Scripts\python.exe gpu_node.py    # Ctrl+C after accepting the prompt
```

Then register it to start at logon:

```powershell
$repo = "d:\ContentSummarizer"
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\pythonw.exe" `
                                  -Argument "gpu_node.py" `
                                  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "ContentSummarizerGPUNode" `
    -Action $action -Trigger $trigger -Settings $settings
Start-ScheduledTask -TaskName ContentSummarizerGPUNode
```

Logs go to `gpu_node.log` in the repo. If `http://MAINPC:8788` doesn't resolve
from the mini PC, use the desktop's IP address in `GPU_NODE_URL` instead
(`ipconfig` on the desktop; consider a DHCP reservation in your router).

## 7. Turn off the desktop's 6 pm task

Once the mini PC worker is confirmed running, on the **desktop** run:

```powershell
Disable-ScheduledTask -TaskName ContentSummarizer
```

Two drainers polling the same queue can race on the same issue — keep exactly
one active.

## Updating the code later

```powershell
cd $env:USERPROFILE\ContentSummarizer
git pull --ff-only
Stop-ScheduledTask -TaskName ContentSummarizerWorker
Start-ScheduledTask -TaskName ContentSummarizerWorker
```

(The worker itself runs `git pull --ff-only` after each successful drain so
committed summaries appear on disk for the dashboard; code updates just need
the restart above.)

## How failures behave

A video that fails gets a `summarize-failed` label plus an error comment, and
stays open. The worker skips labeled issues for `RETRY_FAILED_HOURS` (default
24 h), then retries automatically. The dashboard's **Retry now** button
removes the label and retries immediately. A manual `python drain.py` retries
everything regardless of labels.

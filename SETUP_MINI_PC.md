# Mini PC setup — always-on worker + dashboard

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
The mini PC has no CUDA GPU, so it should use the default `openai` backend.

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

## 6. Turn off the desktop's 6 pm task

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

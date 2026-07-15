# Self-hosted runner mode (event-driven, home IP)

The most robust setup: GitHub still fires the workflow the instant an issue is
opened, but the job runs on a **self-hosted runner on a home PC** instead of a
GitHub-hosted cloud runner. Why: GitHub's datacenter IPs get blocked by
YouTube ("Sign in to confirm you're not a bot"); your home IP doesn't. The
runner reuses this repo's local `venv`, `.env`, and GPU, so there are no
per-run installs, no repo secrets, and caption-less videos can transcribe on
the local GPU.

Workflow: [.github/workflows/summarize.yml](.github/workflows/summarize.yml)
(`runs-on: [self-hosted, windows]`, one step that runs
`.venv\Scripts\python.exe drain.py` in the repo dir).

## What's installed on this desktop (MAINPC)

- Runner package at `C:\Users\aaron\actions-runner` (v2.335.1), registered to
  `Isidore94/ContentSummarizer` as **mainpc** with labels `self-hosted,
  windows`.
- The job runs `drain.py` from `D:\ContentSummarizer`, which loads `.env`
  (all keys + `TRANSCRIBE_BACKEND=local` → uses the RTX 3080 Ti for
  caption-less videos) and authenticates to GitHub with the local PAT.

## IMPORTANT — make the runner permanent

If you started the runner interactively (`run.cmd`), it stops when that window
/ session closes. Pick one of these so it survives logoff and reboot:

### Option A — start at logon (no admin, full GPU access) — recommended

Runs the runner as **you** at every logon, so it has your venv, `.env`, GPU,
and the already-downloaded Whisper model cache. Paste in PowerShell:

```powershell
$startup = [Environment]::GetFolderPath('Startup')
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\ContentSummarizerRunner.lnk")
$sc.TargetPath = "C:\Users\aaron\actions-runner\run.cmd"
$sc.WorkingDirectory = "C:\Users\aaron\actions-runner"
$sc.Save()
"Runner will now auto-start at logon."
```

For a machine meant to stay up (a mini PC), also enable automatic sign-in
(`netplwiz`) so it comes back after a reboot without you logging in.

### Option B — Windows service (headless, no login needed)

Survives reboots without anyone logged in, but **must be installed to run as
your user account** — the default `NETWORK SERVICE` account has a different
home directory, so it would re-download the 3 GB Whisper model to its own
cache and may not see the GPU or `.env`. In an **elevated** PowerShell:

```powershell
cd C:\Users\aaron\actions-runner
.\svc.cmd install <YourWindowsUser>   # e.g. .\svc.cmd install aaron  (prompts for your password)
.\svc.cmd start
```

Only run one at a time — if the service is running, don't also run `run.cmd`
(and vice-versa); the runner identity can only have one active listener.

## Managing the runner

- **Status / logs:** the `run.cmd` console shows `Listening for Jobs` and each
  job; service logs are under `C:\Users\aaron\actions-runner\_diag`.
- **Stop:** Ctrl+C in the `run.cmd` window, or `.\svc.cmd stop` for the service.
- **Remove/re-register:** `.\config.cmd remove --token <new-removal-token>`
  (get the token from Settings → Actions → Runners).
- **See it in GitHub:** repo → Settings → Actions → Runners (should show
  **mainpc – Idle/Active**).

## Updating the code later

The job runs the **local clone** at `D:\ContentSummarizer`, so to pick up new
`drain.py`/`pipeline.py` changes, pull on the runner box:

```powershell
cd D:\ContentSummarizer
git pull --ff-only
```

(The workflow file itself is read from GitHub's default branch, so workflow
changes take effect on push without a pull.)

## Moving the runner to the mini PC later

Do the mini-PC install ([SETUP_MINI_PC.md](SETUP_MINI_PC.md)) so it has the
repo + venv + `.env`, register a runner there the same way, and it takes over.
The mini PC has no GPU, so leave `TRANSCRIBE_BACKEND` at its default (`auto`)
so caption-less videos use the desktop GPU node when it's on, else the OpenAI
audio API. If the repo lives at a different path there, update
`working-directory` in the workflow.

## Only one drainer at a time

With the runner active, disable the desktop's old daily task so they don't
double-process:

```powershell
Disable-ScheduledTask -TaskName ContentSummarizer
```

And don't also run the mini-PC polling worker (`dashboard.py`/`worker.py`) —
the self-hosted runner is now the queue drainer.

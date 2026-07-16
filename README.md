# ContentSummarizer

A personal YouTube-to-summary pipeline. Drop YouTube links into this repo's
**GitHub Issues** from your phone; the Windows desktop app listens to the queue,
summarizes each video, saves a plain-text copy in a folder you choose, and
commits the summary back to the repo.

## How it works

- **Queue** = this repo's GitHub Issues. One open issue per video; the issue
  **title is the YouTube URL**.
- **Drainer** (`drain.py`) = run by Windows Task Scheduler. Lists open issues,
  runs the pipeline on each, commits a summary file, pastes the summary as a
  closing comment, and closes the issue.
- **Pipeline** (`pipeline.py`):
  1. Fetch existing captions with `yt-dlp`
     (`--write-auto-sub --write-subs --skip-download --sub-format vtt`,
     preferring manual subs over auto). VTT timestamps/markup are stripped to
     plain text.
  2. **Fallback:** if there are no captions, download `bestaudio` with `yt-dlp`
     and transcribe locally with `faster-whisper` (`large-v3`, `device="cuda"`,
     `compute_type="float16"`). Set `FORCE_WHISPER=1` to always skip captions.
  3. Summarize the transcript with the Anthropic API (`claude-haiku-4-5`) into a
     one-line TL;DR, key-point bullets, and notable claims/takeaways.
  4. Return readable plain text in the selected Simple, Detailed, or Complex
     format.

Repository copies are written to
`summaries/<sanitized-title>--<youtube-id>.txt`. The desktop app also writes
them directly to the folder selected in its GUI, including Google Drive for
desktop folders.

## Repo layout

```
desktop_gui.py      # Windows GUI + queue listener
drain.py            # queue drainer
pipeline.py         # URL -> captions/whisper -> model -> plain text
requirements.txt
.env.example
.gitignore
README.md
MOBILE_SHORTCUT.md  # iOS Shortcut recipe to add videos from your phone
summaries/          # generated summaries land here
```

## Setup (Windows desktop, RTX 3080 Ti / CUDA)

1. **Install Python 3.10+** and create a virtualenv:

   ```powershell
   git clone https://github.com/<owner>/ContentSummarizer.git
   cd ContentSummarizer
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Install ffmpeg** (required by yt-dlp for the audio fallback) and make sure
   it's on your `PATH`: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`).

3. **GPU / CUDA:** `faster-whisper` runs `large-v3` on the GPU. It needs the
   cuBLAS and cuDNN libraries for CUDA on your `PATH`. If you have an NVIDIA
   CUDA/cuDNN install this usually just works; otherwise the simplest fix is:

   ```powershell
   pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
   ```

   (Only the whisper fallback needs the GPU; the caption path does not.)

4. **Create your `.env`** from the template and fill in the values:

   ```powershell
   copy .env.example .env
   ```

## `.env` configuration

| Variable            | Required | Description                                                        |
| ------------------- | -------- | ------------------------------------------------------------------ |
| `GITHUB_TOKEN`      | yes      | Fine-grained PAT scoped to this repo (see scopes below).           |
| `GITHUB_REPO`       | yes      | The repo the queue lives in, as `owner/repo`.                      |
| `OPENAI_API_KEY`    | usually  | Required when `SUMMARY_PROVIDER=openai` (the default).             |
| `ANTHROPIC_API_KEY` | alternate| Required only when `SUMMARY_PROVIDER=anthropic`.                   |
| `FORCE_WHISPER`     | no       | Set to `1` to always transcribe with Whisper and skip captions.   |
| `SUMMARY_DETAIL`    | no       | `simple`, `detailed`, or `complex` (GUI choice overrides this).   |
| `SUMMARY_FOLDER`    | no       | Output folder for CLI/server runs; the GUI has a folder picker.   |

`.env` is git-ignored (it's the first entry in `.gitignore`). **Only ever commit
`.env.example` with empty placeholders — never real keys.**

### Required GitHub PAT scopes

Create a **fine-grained personal access token** at
**GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens**:

- **Resource owner:** your account.
- **Repository access:** *Only select repositories* → **this repo only**.
- **Repository permissions:**
  - **Issues:** **Read and write** (list, comment on, and close issues).
  - **Contents:** **Read and write** (commit summary files).

That's the complete set — no other permissions are needed. The same token is
used by the iOS Shortcut to create issues (see `MOBILE_SHORTCUT.md`).

## Run it manually

```powershell
.venv\Scripts\activate
python drain.py
```

Each open issue is processed in order. On success the summary is committed,
posted as a closing comment, and the issue is closed. If a video fails, it's
logged, the error is posted as a comment, the issue is **left open**, and the
batch moves on.

## Windows Task Scheduler (schedule ~6pm, drain on next wake)

Create a task that runs the drainer once a day and catches up if the machine was
asleep at the scheduled time.

**Program/script:** `C:\path\to\ContentSummarizer\.venv\Scripts\python.exe`
**Arguments:** `drain.py`
**Start in:** `C:\path\to\ContentSummarizer`

Settings to enable (via `taskschd.msc`, or import the XML below):

1. **Triggers → New → Daily**, start time **6:00 PM**.
2. **General → Run whether user is logged on or not** (so it runs headless).
3. **Settings → Run task as soon as possible after a scheduled start is
   missed** — this drains the queue on the next opportunity if 6pm was missed.
4. **Conditions → Wake the computer to run this task** — combined with (3), the
   task fires as soon as the machine wakes if it was asleep at 6pm.
5. **Conditions →** untick *Start the task only if the computer is on AC power*
   if you want it to run on battery too.

You can create it from PowerShell instead:

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\path\to\ContentSummarizer\.venv\Scripts\python.exe" `
                                    -Argument "drain.py" `
                                    -WorkingDirectory "C:\path\to\ContentSummarizer"
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `                 # run ASAP after a missed start
    -WakeToRun                            # wake the machine to run
Register-ScheduledTask -TaskName "ContentSummarizer" `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

> `-StartWhenAvailable` maps to *"Run task as soon as possible after a scheduled
> start is missed"*, and `-WakeToRun` maps to *"Wake the computer to run this
> task"*. Together they ensure the queue drains on the next wake even if the
> desktop was asleep at 6pm.

## Running modes

There are several ways to drain the queue — pick one (don't run more than one,
or they'll double-process):

1. **Windows desktop app — `ContentSummarizer.exe`** — the recommended mini-PC
   option. It listens to the queue, shows activity, lets you choose a
   local/Google Drive folder, and offers three levels of detail. See
   **[EXE_SETUP.md](EXE_SETUP.md)**.
2. **Local server — `python serve.py`** — source-based worker plus LAN web
   dashboard. See below.
3. **Self-hosted runner (event-driven, home IP)** — GitHub fires the workflow
   on issue-open; the job runs on a runner on a home PC. Instant (~3 s) but not
   a standalone script. See **[SETUP_SELF_HOSTED.md](SETUP_SELF_HOSTED.md)**.
4. **Cloud (event-driven, serverless)** — GitHub Actions on hosted runners;
   nothing at home runs, but YouTube blocks GitHub's datacenter IPs for many
   videos (needs cookies). See **[SETUP_CLOUD.md](SETUP_CLOUD.md)**.
5. **Always-on mini PC** / **Scheduled desktop task** — see the mini-PC guide
   and the Task Scheduler section.

## Windows desktop app (`ContentSummarizer.exe`) — recommended for the mini PC

Build it with `build_exe.ps1`, then copy the contents of `dist/` and your
private `.env` to the mini PC. Full instructions: **[EXE_SETUP.md](EXE_SETUP.md)**.

The executable deliberately does not embed `.env`, ffmpeg, or the local CUDA
stack. It supports caption extraction plus the OpenAI/remote-GPU transcription
backends used by the mini PC.

## Local server (`serve.py`)

```powershell
.venv\Scripts\python.exe serve.py
```

One process does everything:

- **Worker** polls the GitHub Issues queue every `POLL_INTERVAL_SECONDS`
  (default 30) and summarizes new videos — captions via yt-dlp from your home
  IP (no bot-block), caption-less via the local GPU (`TRANSCRIBE_BACKEND=local`)
  or the OpenAI audio API.
- **Dashboard** at `http://<this-pc>:8787` — queue view, paste-a-URL box,
  *Drain now*, per-video *Retry*, and a searchable summary reader that
  auto-syncs from the repo.
- Startup prints the local + LAN URLs. Stop with Ctrl+C.
- Later, bundle it: `pyinstaller --onefile serve.py` → `dist\serve.exe`.

To keep it running across reboots, add `serve.py` to a startup shortcut (same
idea as [SETUP_SELF_HOSTED.md](SETUP_SELF_HOSTED.md)'s runner shortcut) or run
it as a service.

## Cloud mode (GitHub Actions)

The most hands-off option: [.github/workflows/summarize.yml](.github/workflows/summarize.yml)
runs `drain.py` on a GitHub-hosted runner whenever an issue is opened
(and once daily as a backstop). Transcription of caption-less videos uses the
OpenAI audio API (no GPU in the cloud). Setup is three GitHub settings — add
the `OPENAI_API_KEY` secret, allow write permissions, done. Full guide:
**[SETUP_CLOUD.md](SETUP_CLOUD.md)**.

## Always-on mode (mini PC + dashboard)

The local server above (`serve.py`) is the same worker + dashboard; run it on
any always-on box (e.g. a mini PC) — no GPU needed:

- **Worker**: polls the queue every `POLL_INTERVAL_SECONDS` (default 120) and
  summarizes videos as they arrive.
- **Dashboard**: a LAN web UI on port 8787 — queue view, paste-a-URL box,
  *Drain now*, per-video *Retry*, and browsable/searchable summaries.
- **Transcription without a GPU**: with `TRANSCRIBE_BACKEND=auto` (the
  default), caption-less videos use the desktop's GPU node (`gpu_node.py`,
  below) whenever that PC is on, else the OpenAI audio API. `openai`, `local`
  (CUDA faster-whisper), and `remote` pin a specific backend.
- **Borrowable GPU**: run `gpu_node.py` on the PC with the CUDA GPU and set
  `GPU_NODE_URL` — the worker borrows that GPU whenever the machine is
  powered on. The dashboard shows the node online/offline and can pin the
  mode (Auto / OpenAI API / GPU PC).
- **Failure handling**: failed videos get the `summarize-failed` label and are
  retried after `RETRY_FAILED_HOURS` (default 24), or immediately via the
  dashboard's Retry button. A manual `python drain.py` retries everything.

See **[SETUP_MINI_PC.md](SETUP_MINI_PC.md)** for the full setup (and remember
to disable the desktop's scheduled task once the always-on worker takes over —
one drainer at a time).

## Adding videos from your phone

See **[MOBILE_SHORTCUT.md](MOBILE_SHORTCUT.md)** for the iOS Shortcut recipe:
Share Sheet → POST to the GitHub create-issue API with the URL as the title.

## Notes

- **GitHub client:** this uses `requests` against the GitHub REST API directly
  (rather than PyGithub) — the calls are few and explicit (list issues, commit
  via the Contents API, comment, close), and it keeps the dependency surface
  small.
- Owner/repo is read from `GITHUB_REPO` in `.env`, not hardcoded.

# Cloud mode — summarize in GitHub Actions (no home machine)

> ⚠️ **Heads up:** GitHub-hosted runners use datacenter IPs that YouTube often
> blocks with *"Sign in to confirm you're not a bot,"* so many videos fail
> unless you add cookies (see the "Known limitation" section below). For a
> more reliable event-driven setup that runs from your home IP, use a
> self-hosted runner instead — **[SETUP_SELF_HOSTED.md](SETUP_SELF_HOSTED.md)**.


Event-driven, fully serverless. When a YouTube link is added to the queue (an
issue is opened — e.g. by the iOS Shortcut), GitHub Actions runs `drain.py` on
a hosted runner: it summarizes the video, commits the plain-text summary to
`summaries/`, comments it on the issue, and closes it. Nothing at home needs
to run.

Workflow: [.github/workflows/summarize.yml](.github/workflows/summarize.yml).

## One-time setup (all on GitHub, ~3 minutes)

### 1. Add your OpenAI key as a repository secret

The runner has no `.env`; it reads secrets from GitHub.

- Repo → **Settings → Secrets and variables → Actions → New repository secret**
- **Name:** `OPENAI_API_KEY`
- **Value:** your `sk-proj-...` key
- Add secret.

(The `GITHUB_TOKEN` the workflow uses is provided automatically — no secret
needed for it. `GITHUB_REPO` is filled in automatically from the repo name.)

### 2. Allow the workflow to write

- Repo → **Settings → Actions → General → Workflow permissions**
- Select **Read and write permissions** → **Save**.

This lets the run commit summaries and close issues. (The workflow also
requests these scopes explicitly, but this setting removes any ambiguity and
avoids a `403 Resource not accessible by integration` error.)

### 3. Make sure Actions are enabled

- Repo → **Settings → Actions → General → Actions permissions** → **Allow all
  actions** (or at least allow this repository's workflows). New repos have
  this on by default.

## Test it

Two ways:

- **Manual:** repo → **Actions → "Summarize queued videos" → Run workflow**.
  With an empty queue it should finish green in ~1 minute (log: `0 open
  issue(s) to drain`).
- **Real:** open an issue with a YouTube URL as the title (or share one from
  the iOS Shortcut). Within a few seconds the workflow starts; watch it under
  the **Actions** tab. When it's done the issue is closed with the summary as
  a comment, and the file is in `summaries/`.

## Cost

- **Summaries + transcription:** OpenAI usage on your key. Summaries are
  fractions of a cent (gpt-4o-mini). Only *caption-less* videos hit the audio
  API (~$0.006/audio-minute), and most videos have captions, so this is rare.
- **Actions minutes:** private repos get 2,000 free minutes/month; each run is
  ~2 minutes. Making the repo **public** gives unlimited free Actions minutes
  if you ever get close.

## Known limitation — YouTube may block cloud IPs

`yt-dlp` runs from GitHub's datacenter IPs, which YouTube sometimes challenges
with "Sign in to confirm you're not a bot," especially for the audio-download
(transcription) path. Captions usually still fetch fine. If you hit this on
specific videos, the fix is to supply cookies:

1. Export your YouTube cookies to `cookies.txt` (a browser extension like "Get
   cookies.txt" works).
2. Add the file contents as a repo secret named `YT_COOKIES`.
3. Tell me and I'll wire the workflow to write the cookie file and pass
   `--cookies` to yt-dlp.

Videos that fail get the `summarize-failed` label and an error comment, and
stay open — so nothing is lost; the daily backstop run retries them.

## Don't run two drainers at once

Cloud mode replaces the local drainers. To avoid double-processing, on the
**desktop** disable the old daily task:

```powershell
Disable-ScheduledTask -TaskName ContentSummarizer
```

If you set up the mini␠PC worker, don't run it in cloud mode (or vice-versa) —
pick one. The GPU node is unused in pure cloud mode (cloud transcription uses
the OpenAI API); leaving it running is harmless if you might go hybrid later.

## What triggers a run

- An issue is **opened** or **reopened** (the queue gets a new/retried video).
- **Manually** via the Actions tab (`workflow_dispatch`).
- **Daily at 07:00 UTC** as a backstop, catching anything a missed event left
  in the queue.

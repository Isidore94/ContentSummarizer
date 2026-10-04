# iOS Shortcut — add a YouTube video to the queue

This Shortcut lets you share a YouTube link from anywhere on your phone straight
into the queue. It creates a GitHub Issue whose **title is the video URL**,
which `drain.py` later picks up.

## What it does

Share Sheet (a URL) → **POST** to the GitHub create-issue API for this repo with
the URL as the issue title → confirmation banner.

## The GitHub endpoint

```
POST https://api.github.com/repos/Isidore94/ContentSummarizer/issues
```

Headers:

| Header                  | Value                             |
| ----------------------- | --------------------------------- |
| `Authorization`         | `Bearer {YOUR_GITHUB_TOKEN}`      |
| `Accept`                | `application/vnd.github+json`     |
| `X-GitHub-Api-Version`  | `2022-11-28`                      |
| `Content-Type`          | `application/json`                |

JSON body:

```json
{
  "title": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

`title` is the only required field — set it to the shared URL. GitHub responds
`201 Created` with the new issue object on success.

### Optional: a custom AI prompt

Add a `body` field to tailor that one summary — the drainer treats an issue body
as the AI instruction:

```json
{
  "title": "https://www.youtube.com/watch?v=VIDEO_ID",
  "body": "Focus on the investing advice and list every ticker mentioned."
}
```

To be asked each time, insert an **Ask for Input** (Text, e.g. *"Prompt? (leave
blank for normal)"*) action before **Get Contents of URL** and use its result as
`body`. A blank answer gives the normal summary. The same box exists in the
desktop window and the web GUI.

### Optional: pick the detail level per video

Start the `body` with a `detail:` line (`simple`, `detailed` or `complex`;
`level:` also works) on its own line, before any prompt:

```json
{
  "title": "https://www.youtube.com/watch?v=VIDEO_ID",
  "body": "detail: complex\nFocus on the numbers."
}
```

The prompt after it is optional. Without a `detail:` line the drainer's
default level applies. To choose each time, add a **Choose from Menu** action
(prompt *"Detail?"*) with four items — **Default**, **Simple**, **Detailed**,
**Complex** — and under each item a **Text** action: empty for Default,
otherwise `detail: simple` / `detail: detailed` / `detail: complex`. Then add
an **Ask for Input** (the optional prompt) and a final **Text** action that
joins the menu result, a new line, and the prompt; use that as `body`. An
unknown level is ignored (logged), not an error.

A `detail:` line also forces a fresh summary even if that video was already
summarized (otherwise an existing summary is reused).

### Optional: the transcript instead of a summary

Put this marker line in the `body` to skip the AI entirely and get the raw
transcript — every word, no model, no API cost:

```json
{
  "title": "https://www.youtube.com/watch?v=VIDEO_ID",
  "body": "<!-- content-summarizer:mode --> raw"
}
```

The transcript comes back as a comment on the issue (truncated if the video is
long) and is saved in full on the machine that ran the job. A body without the
marker summarizes as usual, so nothing about the Shortcut above needs to change.

> **Token:** create a **separate** fine-grained PAT for the phone, scoped to
> this repo with only **Issues: Read and write**. The drainer's token also has
> Contents write (it commits summaries); a phone is the device most likely to
> be lost, so it shouldn't carry that. Keep the token in the Shortcut only —
> don't share the Shortcut with the token embedded.

## Building the Shortcut

1. Open **Shortcuts → + (new)** → rename it e.g. *"Summarize this video"*.
2. Tap the shortcut's **(i) info** button → enable **Show in Share Sheet**, and
   set **Share Sheet Types** to **URLs** only.
3. Add these actions in order:

   1. **Receive** *URLs* input from the Share Sheet.
      (In *"If there's no input"*, choose **Stop and respond** or **Ask for
      URL** so you can also run it manually.)
   2. **Text** — set its content to the **Shortcut Input** (the shared URL).
      This becomes the issue title. *(Optional: use a "Get URLs from Input"
      action first to make sure you pass a clean URL string.)*
   3. **Get Contents of URL** — configure:
      - **URL:** `https://api.github.com/repos/Isidore94/ContentSummarizer/issues`
      - **Method:** `POST`
      - **Headers:** add the four headers from the table above (put your token
        in the `Authorization` value as `Bearer ghp_...`).
      - **Request Body:** **JSON**, with one field:
        - key `title` → value: the **Text** from step 2.
   4. **Show Notification** (or **Show Result**) — e.g. *"Queued for
      summary ✅"* — so you get a confirmation.

4. **Run it** once from the Share Sheet on a YouTube video to test. Check that a
   new open issue appears in the repo with the URL as its title.

## Equivalent curl (for testing the endpoint)

```bash
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/Isidore94/ContentSummarizer/issues \
  -d '{"title":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

A `201 Created` response means the video is queued. The next scheduled run of
`drain.py` (or a manual `python drain.py`) will summarize it, commit the
plain-text output, comment the summary, and close the issue.

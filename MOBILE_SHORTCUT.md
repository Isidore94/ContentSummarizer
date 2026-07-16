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

> **Token:** use the same fine-grained PAT as the drainer (scoped to this repo,
> **Issues: Read and write**). Creating issues only needs the Issues write
> permission. Keep the token in the Shortcut only — don't share the Shortcut
> with the token embedded.

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

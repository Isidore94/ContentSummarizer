"""serve.py — run ContentSummarizer as a local server.

One command boots the whole system:

    python serve.py

It polls the GitHub Issues queue, summarizes each new video from THIS machine's
IP (so YouTube doesn't block it) using the local GPU when available, commits the
summary, and serves a web dashboard at http://<this-pc>:8787 (queue view,
add-a-video box, searchable summary reader).

Configuration comes from .env (GITHUB_TOKEN, GITHUB_REPO, OPENAI_API_KEY,
TRANSCRIBE_BACKEND, POLL_INTERVAL_SECONDS, DASHBOARD_HOST/PORT, ...). This is
the single entry point intended for packaging into a standalone .exe later
(e.g. with PyInstaller: `pyinstaller --onefile serve.py`).
"""

from dashboard import main

if __name__ == "__main__":
    main()

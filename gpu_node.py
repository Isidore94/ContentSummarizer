"""gpu_node.py — GPU transcription service (run on the PC with the CUDA GPU).

Exposes the local faster-whisper pipeline over HTTP so the always-on worker
(on a GPU-less mini PC) can borrow this machine's GPU whenever it happens to
be powered on. With TRANSCRIBE_BACKEND=auto, the worker probes /health and
prefers this node over the OpenAI audio API; when this PC is off, the worker
falls back to the API automatically.

Endpoints:
  GET  /health      -> {"ok": true, "model": ..., "device": ...}
  POST /transcribe  {"url": "<video url>"} -> {"text": "<transcript>"}

Security: LAN-only by design. Optionally set GPU_NODE_TOKEN in .env on both
machines to require an X-Node-Token header.

Start with `python gpu_node.py` (port GPU_NODE_PORT, default 8788).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import drain
import pipeline

log = logging.getLogger("gpu_node")

load_dotenv()

app = FastAPI()


def _authorized(request: Request):
    token = os.environ.get("GPU_NODE_TOKEN", "").strip()
    return not token or request.headers.get("X-Node-Token") == token


@app.get("/health")
def health(request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "ok": True,
        "model": pipeline.WHISPER_MODEL,
        "device": pipeline.WHISPER_DEVICE,
    }


@app.post("/transcribe")
def transcribe(payload: dict, request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "missing or invalid 'url'"}, status_code=400)
    log.info("transcribing %s", url)
    try:
        text = pipeline.transcribe_local(url)
    except pipeline.PipelineError as exc:
        log.error("transcription failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    log.info("done (%d chars)", len(text))
    return {"text": text}


def main():
    handlers = [
        RotatingFileHandler(
            os.path.join(drain.REPO_DIR, "gpu_node.log"),
            maxBytes=5_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    ]
    if sys.stderr:  # absent under pythonw.exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    host = os.environ.get("GPU_NODE_HOST", "0.0.0.0")
    port = int(os.environ.get("GPU_NODE_PORT") or 8788)
    log.info("GPU node starting on http://%s:%d", host, port)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()

"""pipeline.py — Turn a YouTube URL into a bite-sized markdown summary.

Given a video URL the pipeline (1) fetches existing captions with yt-dlp,
(2) falls back to local faster-whisper transcription when a video has no
captions, and (3) summarizes the transcript with the Anthropic API. It returns
finished markdown ready to commit.
"""

from __future__ import annotations

import glob
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests
from anthropic import Anthropic
from openai import OpenAI

log = logging.getLogger(__name__)

# Models used for summarization, one per provider — both fast and cheap.
ANTHROPIC_SUMMARY_MODEL = "claude-haiku-4-5"
OPENAI_SUMMARY_MODEL_DEFAULT = "gpt-4o-mini"

# Local Whisper settings for the caption-less fallback path.
WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "float16"

# Transcription fallback backend for caption-less videos:
#   "auto"   — GPU node (below) when reachable, else the OpenAI audio API
#   "openai" — OpenAI audio API (no GPU needed)
#   "local"  — faster-whisper on this machine's CUDA GPU
#   "remote" — a gpu_node.py instance at GPU_NODE_URL (fail if unreachable)
TRANSCRIBE_BACKEND_DEFAULT = "auto"
OPENAI_TRANSCRIBE_MODEL_DEFAULT = "whisper-1"

# The OpenAI audio API rejects uploads over 25 MB; we transcode down to mono
# 16 kHz 32 kbps mp3 (fine for speech) and chunk anything still over the cap.
_OPENAI_AUDIO_LIMIT_BYTES = 24 * 1024 * 1024
_AUDIO_CHUNK_SECONDS = 1200

# Guard against a pathologically long transcript blowing past the context window.
MAX_TRANSCRIPT_CHARS = 500_000

# Cached faster-whisper model. Loading large-v3 onto the GPU is expensive, so we
# do it once per process even when a batch needs it repeatedly.
_whisper_model = None


class PipelineError(Exception):
    """Raised when a video cannot be turned into a summary."""


def _run_yt_dlp(args):
    """Run yt-dlp with the given args, returning the CompletedProcess."""
    # Invoke via the current interpreter: the bare "yt-dlp" command is only on
    # PATH when the venv is activated, and Task Scheduler runs python.exe
    # directly without activation.
    cmd = [sys.executable, "-m", "yt_dlp", *args]
    log.debug("running: %s", " ".join(cmd))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise PipelineError(
            "yt-dlp not found. Install it with `pip install yt-dlp`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"yt-dlp failed ({exc.returncode}): {exc.stderr.strip()}"
        ) from exc


def get_metadata(url):
    """Return {"id", "title"} for a video without downloading it."""
    proc = _run_yt_dlp(
        ["--skip-download", "--no-warnings", "--print", "%(id)s\t%(title)s", url]
    )
    first_line = proc.stdout.strip().splitlines()[0]
    vid, _, title = first_line.partition("\t")
    vid = vid.strip()
    return {"id": vid, "title": title.strip() or vid}


def _download_subs(url, workdir, auto):
    """Download subtitles into ``workdir``; return the .vtt path or None."""
    flag = "--write-auto-sub" if auto else "--write-subs"
    _run_yt_dlp(
        [
            "--skip-download",
            flag,
            "--sub-format",
            "vtt",
            "--sub-langs",
            "en.*,en",
            "--no-warnings",
            "-o",
            os.path.join(workdir, "%(id)s.%(ext)s"),
            url,
        ]
    )
    vtts = sorted(glob.glob(os.path.join(workdir, "*.vtt")))
    return vtts[0] if vtts else None


def fetch_captions(url):
    """Fetch captions for a video, preferring manual subs over auto-generated.

    Returns plain text, or None when the video has no captions at all.
    """
    with tempfile.TemporaryDirectory() as workdir:
        path = _download_subs(url, workdir, auto=False)
        source = "manual"
        if not path:
            path = _download_subs(url, workdir, auto=True)
            source = "auto"
        if not path:
            return None
        log.info("using %s captions", source)
        with open(path, encoding="utf-8") as fh:
            return strip_vtt(fh.read())


_TAG_RE = re.compile(r"<[^>]+>")


def strip_vtt(raw):
    """Strip VTT timestamps and markup, returning plain, deduplicated text."""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line:  # cue timing line (may carry align/position settings)
            continue
        if line.isdigit():  # numeric cue index
            continue
        line = _TAG_RE.sub("", line)  # inline <c> / <00:00:00.480> tags
        line = html.unescape(line).strip()
        if not line:
            continue
        # Auto-subs repeat the rolling last line — collapse consecutive dupes.
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _register_cuda_dlls():
    """Windows: the pip-installed CUDA libs (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) live in site-packages/nvidia/*/bin, which is on no DLL
    search path — register those dirs so ctranslate2 can load them."""
    if os.name != "nt":
        return
    import sysconfig

    site_packages = sysconfig.get_paths()["purelib"]
    for lib in ("cublas", "cudnn", "cuda_nvrtc"):
        bin_dir = os.path.join(site_packages, "nvidia", lib, "bin")
        if os.path.isdir(bin_dir):
            os.add_dll_directory(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def _get_whisper_model():
    """Load (once) and return the cached faster-whisper model."""
    global _whisper_model
    if _whisper_model is None:
        _register_cuda_dlls()
        from faster_whisper import WhisperModel  # heavy import; load lazily

        log.info("loading faster-whisper %s on %s", WHISPER_MODEL, WHISPER_DEVICE)
        _whisper_model = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
        )
    return _whisper_model


def _download_audio(url, workdir):
    """Download bestaudio into ``workdir`` and return the file path."""
    _run_yt_dlp(
        [
            "-f",
            "bestaudio",
            "--no-warnings",
            "-o",
            os.path.join(workdir, "%(id)s.%(ext)s"),
            url,
        ]
    )
    files = [os.path.join(workdir, f) for f in os.listdir(workdir)]
    if not files:
        raise PipelineError("audio download produced no file")
    return files[0]


def transcribe_local(url):
    """Download the audio and transcribe it locally with faster-whisper."""
    with tempfile.TemporaryDirectory() as workdir:
        audio = _download_audio(url, workdir)
        model = _get_whisper_model()
        log.info("transcribing audio with local whisper (this can take a while)")
        segments, _ = model.transcribe(audio)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise PipelineError("whisper produced an empty transcript")
        return text


def _ffmpeg(args):
    """Run ffmpeg with the given args, raising PipelineError on failure."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise PipelineError(
            "ffmpeg not found on PATH (required for the transcription fallback)"
        )
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise PipelineError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")


def transcribe_openai(url):
    """Download the audio and transcribe it with the OpenAI audio API.

    No GPU needed — this is the fallback of choice for boxes without CUDA
    (e.g. an always-on mini PC).
    """
    model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", OPENAI_TRANSCRIBE_MODEL_DEFAULT)
    client = OpenAI()
    with tempfile.TemporaryDirectory() as workdir:
        raw = _download_audio(url, workdir)
        # Speech survives heavy compression: mono 16 kHz 32 kbps keeps ~2 h of
        # audio under the API's upload cap.
        small = os.path.join(workdir, "audio.mp3")
        _ffmpeg(["-y", "-i", raw, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", small])

        if os.path.getsize(small) <= _OPENAI_AUDIO_LIMIT_BYTES:
            chunks = [small]
        else:
            _ffmpeg(
                [
                    "-y",
                    "-i",
                    small,
                    "-f",
                    "segment",
                    "-segment_time",
                    str(_AUDIO_CHUNK_SECONDS),
                    "-c",
                    "copy",
                    os.path.join(workdir, "chunk_%03d.mp3"),
                ]
            )
            chunks = sorted(glob.glob(os.path.join(workdir, "chunk_*.mp3")))

        parts = []
        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                log.info("transcribing chunk %d/%d", i + 1, len(chunks))
            try:
                with open(chunk, "rb") as fh:
                    parts.append(
                        client.audio.transcriptions.create(model=model, file=fh).text
                    )
            except Exception as exc:  # openai.APIError and friends
                raise PipelineError(f"OpenAI transcription failed: {exc}") from exc

        text = " ".join(p.strip() for p in parts).strip()
        if not text:
            raise PipelineError("openai transcription produced an empty transcript")
        return text


def _gpu_node_url():
    return os.environ.get("GPU_NODE_URL", "").strip().rstrip("/")


def _gpu_node_headers():
    token = os.environ.get("GPU_NODE_TOKEN", "").strip()
    return {"X-Node-Token": token} if token else {}


def gpu_node_online():
    """True when a gpu_node.py instance is reachable at GPU_NODE_URL."""
    url = _gpu_node_url()
    if not url:
        return False
    try:
        return requests.get(
            f"{url}/health", headers=_gpu_node_headers(), timeout=1.5
        ).ok
    except requests.RequestException:
        return False


def transcribe_remote(url):
    """Transcribe by delegating to a gpu_node.py instance on the LAN."""
    node = _gpu_node_url()
    if not node:
        raise PipelineError("TRANSCRIBE_BACKEND=remote but GPU_NODE_URL is not set")
    log.info("transcribing via GPU node %s", node)
    try:
        resp = requests.post(
            f"{node}/transcribe",
            json={"url": url},
            headers=_gpu_node_headers(),
            timeout=(5, 3600),  # transcription of long videos takes a while
        )
    except requests.RequestException as exc:
        raise PipelineError(f"GPU node request failed: {exc}") from exc
    if resp.status_code != 200:
        raise PipelineError(f"GPU node error ({resp.status_code}): {resp.text[:500]}")
    text = (resp.json().get("text") or "").strip()
    if not text:
        raise PipelineError("GPU node returned an empty transcript")
    return text


def transcribe(url):
    """Transcribe a video's audio using the configured fallback backend."""
    backend = os.environ.get(
        "TRANSCRIBE_BACKEND", TRANSCRIBE_BACKEND_DEFAULT
    ).strip().lower()
    if backend == "auto":
        if gpu_node_online():
            backend = "remote"
        else:
            if _gpu_node_url():
                log.info("GPU node offline; using the OpenAI audio API")
            backend = "openai"
    if backend == "remote":
        return transcribe_remote(url)
    if backend == "openai":
        log.info("transcribing via the OpenAI audio API")
        return transcribe_openai(url)
    if backend == "local":
        return transcribe_local(url)
    raise PipelineError(
        f"Unknown TRANSCRIBE_BACKEND {backend!r}; "
        "expected 'auto', 'openai', 'local', or 'remote'"
    )


_SUMMARY_SYSTEM = (
    "You summarize video transcripts into tight, skimmable notes. "
    "Be accurate and concise — no filler, no preamble."
)

_SUMMARY_INSTRUCTIONS = """\
Summarize the transcript below as compact markdown with exactly these sections:

**TL;DR:** one sentence capturing the whole video.

**Key points**
- 3-7 short bullets covering the main ideas.

**Notable claims & takeaways**
- Any specific claims, numbers, recommendations, or surprising takeaways.
- Drop this section entirely if there genuinely aren't any.

Keep it bite-sized. Do not repeat the title or add a top-level heading of your own.

TRANSCRIPT:
"""


def _summarize_anthropic(transcript, api_key):
    client = Anthropic(api_key=api_key) if api_key else Anthropic()
    try:
        response = client.messages.create(
            model=ANTHROPIC_SUMMARY_MODEL,
            max_tokens=1024,
            system=_SUMMARY_SYSTEM,
            messages=[
                {"role": "user", "content": _SUMMARY_INSTRUCTIONS + transcript}
            ],
        )
    except Exception as exc:  # anthropic.APIError and friends
        raise PipelineError(f"Anthropic API call failed: {exc}") from exc

    return next((b.text for b in response.content if b.type == "text"), "").strip()


def _summarize_openai(transcript, api_key):
    model = os.environ.get("OPENAI_SUMMARY_MODEL", OPENAI_SUMMARY_MODEL_DEFAULT)
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    try:
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": _SUMMARY_INSTRUCTIONS + transcript},
            ],
        )
    except Exception as exc:  # openai.APIError and friends
        raise PipelineError(f"OpenAI API call failed: {exc}") from exc

    return (response.choices[0].message.content or "").strip()


def summarize(transcript, api_key=None):
    """Summarize a transcript; return the markdown body.

    Provider is picked by the SUMMARY_PROVIDER env var ("openai" or
    "anthropic"), read lazily so it honors .env values loaded after import.
    """
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        log.warning(
            "transcript is %d chars; trimming to %d before summarizing",
            len(transcript),
            MAX_TRANSCRIPT_CHARS,
        )
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]

    provider = os.environ.get("SUMMARY_PROVIDER", "openai").strip().lower()
    if provider == "openai":
        return _summarize_openai(transcript, api_key)
    if provider == "anthropic":
        return _summarize_anthropic(transcript, api_key)
    raise PipelineError(
        f"Unknown SUMMARY_PROVIDER {provider!r}; expected 'openai' or 'anthropic'"
    )


def summarize_video(url, force_whisper=False, api_key=None):
    """Turn a YouTube URL into finished summary markdown.

    Returns {"title": ..., "markdown": ...}. Raises PipelineError on failure.
    """
    meta = get_metadata(url)
    title = meta["title"]

    transcript = None
    if force_whisper:
        log.info("force_whisper set; skipping captions")
    else:
        transcript = fetch_captions(url)

    if not transcript:
        if not force_whisper:
            log.info("no captions found; falling back to transcription")
        transcript = transcribe(url)

    body = summarize(transcript, api_key=api_key)
    if not body:
        raise PipelineError("summarizer returned empty output")

    markdown = f"# {title}\n\n<{url}>\n\n{body}\n"
    return {"title": title, "markdown": markdown}

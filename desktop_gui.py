"""Windows desktop GUI for the always-on ContentSummarizer listener."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import shutil
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dashboard
import drain
import pipeline
from app_config import (
    DETAIL_LEVELS,
    data_dir,
    executable_dir,
    load_runtime_env,
    load_settings,
    normalize_detail,
    save_settings,
    settings_path,
)
from worker import Worker

log = logging.getLogger("desktop_gui")
_mutex_handle = None

# A freshly written summary stays highlighted in the list for this long.
FRESH_SECONDS = 300
PREVIEW_CHAR_LIMIT = 400_000

DOT_LISTENING = "#1f9d4d"
DOT_PAUSED = "#c98a00"
DOT_OFF = "#8a8a8a"
TOAST_COLORS = {"info": "#3c3c3c", "good": "#1f7a37", "bad": "#b3261e"}


def _one_line(text, limit=160):
    """Flatten text for a single-line label; a stray blob must not resize the UI."""
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _detect_new(previous: set[str], current: list[str]) -> list[str]:
    """Names in `current` that were not in `previous`, newest-first order kept."""
    return [name for name in current if name not in previous]


def _matches_filter(name: str, query: str) -> bool:
    """Every whitespace-separated word must appear somewhere in the file name."""
    haystack = name.lower()
    return all(word in haystack for word in query.lower().split())


def _human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _pretty_title(name: str) -> str:
    """Turn `the-big-iq-controversy--dQw4w9WgXcQ.txt` into `The big iq controversy`.

    drain.summary_filename() writes a lowercase slug plus a `--videoId` suffix,
    so the list can show the title without the machine parts.
    """
    stem = name[:-4] if name.lower().endswith(".txt") else name
    stem = stem.rsplit("--", 1)[0] or stem
    title = stem.replace("-", " ").replace("_", " ").strip()
    if not title:
        return name
    return title[0].upper() + title[1:]


def _clipboard_youtube_url(text: str | None) -> str | None:
    """Return a normalized YouTube URL if the clipboard holds exactly one."""
    candidate = (text or "").strip()
    if not candidate or len(candidate.split()) != 1:
        return None
    try:
        return pipeline.validate_youtube_url(candidate)
    except Exception:
        return None


class GuiLogHandler(logging.Handler):
    def __init__(self, messages: queue.Queue[str]):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        try:
            self.messages.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)


def _acquire_single_instance():
    """Use a named Windows mutex so two workers cannot race each other."""
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(
        None, False, "Local\\ContentSummarizerDesktopApp"
    )
    return bool(_mutex_handle) and kernel32.GetLastError() != 183


def _configure_logging(messages):
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        data_dir() / "ContentSummarizer.log",
        maxBytes=5_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    gui_handler = GuiLogHandler(messages)
    gui_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(gui_handler)


class ContentSummarizerApp:
    def __init__(self, root: tk.Tk, log_messages: queue.Queue[str]):
        self.root = root
        self.log_messages = log_messages
        self.first_run = not settings_path().exists()
        self.settings = load_settings()
        self.worker: Worker | None = None
        self.worker_thread: threading.Thread | None = None
        self.queue_refreshing = False
        self.summary_syncing = False
        self.closing = False
        self.queue_links: dict[str, str] = {}
        self.summary_paths: list[Path] = []
        self.all_summary_paths: list[Path] = []
        self.known_summaries: set[str] | None = None  # None until the first scan
        self.last_summary_names: list[str] = []
        self.preview_path: Path | None = None
        self.toast_token = 0
        self.clipboard_seen = ""
        self.busy = False
        self.lan_url = ""
        self.lan_started = False

        self.folder_var = tk.StringVar(value=str(self.settings["summary_folder"]))
        self.detail_var = tk.StringVar(value=str(self.settings["summary_detail"]))
        self.auto_start_var = tk.BooleanVar(value=bool(self.settings["auto_start"]))
        self.notify_sound_var = tk.BooleanVar(value=bool(self.settings["notify_sound"]))
        self.status_var = tk.StringVar(value="Stopped")
        self.last_poll_var = tk.StringVar(value="Not polled yet")
        self.lan_var = tk.StringVar(value="Web GUI: starts with the listener")
        self.url_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.toast_var = tk.StringVar(value="Ready. Paste a YouTube link above to get started.")
        self.counts_var = tk.StringVar(value="0 done · 0 failed")
        self.summary_count_var = tk.StringVar(value="No summaries yet")

        root.title("ContentSummarizer")
        root.geometry("1180x950")
        root.minsize(900, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self._bind_shortcuts()

        self.root.after(150, self._drain_log_messages)
        self.root.after(300, self._refresh_status)
        self.root.after(500, self._refresh_summaries)
        self.root.after(800, self._queue_refresh_tick)
        self.root.after(1200, self._clipboard_tick)
        if self.first_run:
            self.root.after(200, self.choose_folder)
        if self.auto_start_var.get():
            self.root.after(600, self.start_listener)

    # ---------------------------------------------------------------- layout

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        title_row = ttk.Frame(header)
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.columnconfigure(1, weight=1)
        ttk.Label(
            title_row,
            text="ContentSummarizer",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        status_row = ttk.Frame(title_row)
        status_row.grid(row=0, column=2, sticky="e")
        self.status_dot = tk.Label(
            status_row, text="●", fg=DOT_OFF, font=("Segoe UI", 12)
        )
        self.status_dot.pack(side="left", padx=(0, 4))
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")
        ttk.Label(
            status_row, textvariable=self.counts_var, foreground="#5a5a5a"
        ).pack(side="left", padx=(12, 0))

        ttk.Label(header, textvariable=self.last_poll_var, foreground="#5a5a5a").grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )
        self.progress = ttk.Progressbar(header, mode="indeterminate", length=180)
        self.progress.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.progress.grid_remove()

        lan_row = ttk.Frame(header)
        lan_row.grid(row=3, column=0, sticky="w", pady=(3, 0))
        ttk.Label(lan_row, textvariable=self.lan_var).pack(side="left")
        self.lan_open_button = ttk.Button(
            lan_row, text="Open", width=6, command=self.open_lan_url, state="disabled"
        )
        self.lan_open_button.pack(side="left", padx=(8, 0))
        self.lan_copy_button = ttk.Button(
            lan_row, text="Copy link", width=10, command=self.copy_lan_url,
            state="disabled",
        )
        self.lan_copy_button.pack(side="left", padx=(4, 0))

        settings_box = ttk.LabelFrame(outer, text="Summary settings", padding=12)
        settings_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        settings_box.columnconfigure(1, weight=1)

        ttk.Label(settings_box, text="Save summaries to").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        folder_entry = ttk.Entry(
            settings_box, textvariable=self.folder_var, state="readonly"
        )
        folder_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(settings_box, text="Choose folder...", command=self.choose_folder).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(settings_box, text="Open", command=self.open_summary_folder).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Label(
            settings_box,
            text="A Google Drive for desktop folder works here. Files are saved as .txt.",
            foreground="#5a5a5a",
        ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 10))

        ttk.Label(settings_box, text="Summary style").grid(
            row=2, column=0, sticky="w", padx=(0, 10)
        )
        style_frame = ttk.Frame(settings_box)
        style_frame.grid(row=2, column=1, columnspan=3, sticky="w")
        for detail in DETAIL_LEVELS:
            ttk.Radiobutton(
                style_frame,
                text=detail.title(),
                value=detail,
                variable=self.detail_var,
                command=self.change_detail,
            ).pack(side="left", padx=(0, 16))

        add_box = ttk.LabelFrame(outer, text="Add a video", padding=12)
        add_box.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        add_box.columnconfigure(1, weight=1)

        ttk.Label(add_box, text="Video URL").grid(row=0, column=0, sticky="w", padx=(0, 10))
        url_row = ttk.Frame(add_box)
        url_row.grid(row=0, column=1, columnspan=2, sticky="ew")
        url_row.columnconfigure(0, weight=1)
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda _e: self.queue_video())
        ttk.Button(url_row, text="Paste", width=7, command=self.paste_url).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(url_row, text="Queue it", command=self.queue_video).grid(
            row=0, column=2, padx=(6, 0)
        )

        ttk.Label(add_box, text="AI prompt").grid(
            row=1, column=0, sticky="nw", padx=(0, 10), pady=(8, 0)
        )
        self.prompt_text = tk.Text(add_box, height=2, wrap="word", font=("Segoe UI", 9))
        self.prompt_text.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.prompt_text.bind("<Control-Return>", lambda _e: (self.queue_video(), "break")[1])
        ttk.Label(
            add_box,
            text=(
                "Optional — tailor this one summary, e.g. \"focus on the investing "
                "advice and list every ticker mentioned\". Leave empty for the "
                "normal summary. Ctrl+Enter queues it."
            ),
            wraplength=900,
            justify="left",
            foreground="#5a5a5a",
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))

        controls = ttk.Frame(outer)
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.listen_button = ttk.Button(
            controls, text="Start listening", command=self.toggle_listener
        )
        self.listen_button.pack(side="left")
        ttk.Button(controls, text="Drain now", command=self.drain_now).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(controls, text="Refresh (F5)", command=self.refresh_everything).pack(
            side="left", padx=(8, 0)
        )
        ttk.Checkbutton(
            controls,
            text="Start listening when the app opens",
            variable=self.auto_start_var,
            command=self.change_auto_start,
        ).pack(side="right")
        ttk.Checkbutton(
            controls,
            text="Chime on new summary",
            variable=self.notify_sound_var,
            command=self.change_notify_sound,
        ).pack(side="right", padx=(0, 16))

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.grid(row=4, column=0, sticky="nsew")

        left = ttk.Notebook(panes)
        # A grid, not a nested Panedwindow: a paned window hands the list its
        # requested height only, which collapses it to the filter row.
        right = ttk.Frame(panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=2)
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        # Reading a summary deserves more room than watching the queue.
        self.root.after(250, lambda: self._place_sash(panes))  # after the map

        queue_box = ttk.Frame(left, padding=8)
        activity_box = ttk.Frame(left, padding=8)
        left.add(queue_box, text="Queue")
        left.add(activity_box, text="Activity")

        queue_box.rowconfigure(0, weight=1)
        queue_box.columnconfigure(0, weight=1)
        self.queue_tree = ttk.Treeview(
            queue_box,
            columns=("number", "status", "title"),
            show="headings",
            height=10,
        )
        self.queue_tree.heading("number", text="#")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("title", text="Video URL")
        self.queue_tree.column("number", width=48, anchor="center", stretch=False)
        self.queue_tree.column("status", width=75, stretch=False)
        self.queue_tree.column("title", width=300)
        self.queue_tree.tag_configure("failed", foreground="#b3261e")
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        queue_scroll = ttk.Scrollbar(
            queue_box, orient="vertical", command=self.queue_tree.yview
        )
        queue_scroll.grid(row=0, column=1, sticky="ns")
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.queue_tree.bind("<Double-1>", self.open_selected_issue)
        ttk.Label(
            queue_box,
            text="Double-click a row to open it on GitHub.",
            foreground="#5a5a5a",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        activity_box.rowconfigure(0, weight=1)
        activity_box.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            activity_box,
            height=8,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            activity_box, orient="vertical", command=self.log_text.yview
        )
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        summary_box = ttk.LabelFrame(right, text="Saved summaries", padding=8)
        preview_box = ttk.LabelFrame(right, text="Preview", padding=8)
        summary_box.grid(row=0, column=0, sticky="nsew")
        preview_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        summary_box.rowconfigure(1, weight=1)
        summary_box.columnconfigure(0, weight=1)
        filter_row = ttk.Frame(summary_box)
        filter_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Find").grid(row=0, column=0, padx=(0, 6))
        filter_entry = ttk.Entry(filter_row, textvariable=self.filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(
            filter_row, textvariable=self.summary_count_var, foreground="#5a5a5a"
        ).grid(row=0, column=2, padx=(8, 0))
        self.filter_var.trace_add("write", lambda *_: self._render_summary_list())

        self.summary_tree = ttk.Treeview(
            summary_box, columns=("name", "when"), show="headings", height=5
        )
        self.summary_tree.heading("name", text="Summary")
        self.summary_tree.heading("when", text="Saved")
        self.summary_tree.column("name", width=280)
        self.summary_tree.column("when", width=90, anchor="e", stretch=False)
        self.summary_tree.tag_configure("fresh", foreground="#1f7a37")
        self.summary_tree.grid(row=1, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(
            summary_box, orient="vertical", command=self.summary_tree.yview
        )
        summary_scroll.grid(row=1, column=1, sticky="ns")
        self.summary_tree.configure(yscrollcommand=summary_scroll.set)
        self.summary_tree.bind("<<TreeviewSelect>>", self._on_summary_selected)
        self.summary_tree.bind("<Double-1>", self.open_selected_summary)

        preview_box.rowconfigure(0, weight=1)
        preview_box.columnconfigure(0, weight=1)
        # An explicit height matters: a default 24-line Text requests so much
        # room that the summary list above it collapses to its header.
        self.preview_text = tk.Text(
            preview_box,
            height=6,
            width=40,
            wrap="word",
            state="disabled",
            font=("Segoe UI", 10),
            padx=8,
            pady=6,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(
            preview_box, orient="vertical", command=self.preview_text.yview
        )
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        preview_buttons = ttk.Frame(preview_box)
        preview_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(
            preview_buttons, text="Open in editor", command=self.open_selected_summary
        ).pack(side="left")
        ttk.Button(
            preview_buttons, text="Copy text", command=self.copy_preview
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            preview_buttons, text="Show newest", command=self.show_newest_summary
        ).pack(side="left", padx=(8, 0))
        self._set_preview("Select a summary on the left to read it here.")

        status_bar = ttk.Frame(outer)
        status_bar.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        status_bar.columnconfigure(0, weight=1)
        self.toast_label = tk.Label(
            status_bar, textvariable=self.toast_var, anchor="w", fg=TOAST_COLORS["info"]
        )
        self.toast_label.grid(row=0, column=0, sticky="ew")

    def _place_sash(self, panes):
        try:
            width = panes.winfo_width()
            if width > 400:
                panes.sashpos(0, int(width * 0.42))
        except tk.TclError:
            pass

    def _bind_shortcuts(self):
        self.root.bind("<F5>", lambda _e: self.refresh_everything())
        self.root.bind("<Control-l>", lambda _e: self._focus_url())
        self.root.bind("<Control-L>", lambda _e: self._focus_url())

    def _focus_url(self):
        self.url_entry.focus_set()
        self.url_entry.select_range(0, "end")
        return "break"

    # ---------------------------------------------------------------- toasts

    def _flash(self, message, kind="info"):
        self.toast_token += 1
        token = self.toast_token
        self.toast_var.set(_one_line(message, 200))
        self.toast_label.configure(fg=TOAST_COLORS.get(kind, TOAST_COLORS["info"]))

        def clear():
            if token == self.toast_token and not self.closing:
                self.toast_var.set("")

        self.root.after(9000, clear)

    def _chime(self):
        if not self.notify_sound_var.get() or os.name != "nt":
            return
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            log.debug("could not play the notification sound", exc_info=True)

    # -------------------------------------------------------------- settings

    def _save_settings(self):
        self.settings = {
            "summary_folder": self.folder_var.get(),
            "summary_detail": normalize_detail(self.detail_var.get()),
            "auto_start": self.auto_start_var.get(),
            "notify_sound": self.notify_sound_var.get(),
        }
        try:
            save_settings(self.settings)
        except OSError as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self.root)

    def _configuration_error(self):
        missing = [
            name for name in ("GITHUB_TOKEN", "GITHUB_REPO") if not os.environ.get(name)
        ]
        provider = os.environ.get("SUMMARY_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "anthropic"}:
            return "SUMMARY_PROVIDER must be either 'openai' or 'anthropic' in .env."
        key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        if not os.environ.get(key_name):
            missing.append(key_name)
        if missing:
            return (
                "Missing configuration: "
                + ", ".join(missing)
                + "\n\nPut a completed .env file beside ContentSummarizer.exe, then reopen the app."
            )
        return None

    def _ensure_worker(self):
        if self.worker is not None:
            return True
        error = self._configuration_error()
        if error:
            messagebox.showerror("Configuration needed", error, parent=self.root)
            return False
        folder = Path(self.folder_var.get()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Summary folder unavailable", str(exc), parent=self.root)
            return False
        self._import_legacy_summaries(folder)
        try:
            self.worker = Worker(
                summary_detail=self.detail_var.get(),
                output_dir=str(folder),
                enabled=True,
            )
        except Exception as exc:
            messagebox.showerror("Could not start listener", str(exc), parent=self.root)
            log.exception("worker initialization failed")
            return False
        self.worker_thread = threading.Thread(
            target=self.worker.run_forever,
            daemon=True,
            name="summary-listener",
        )
        self.worker_thread.start()
        self._sync_repository_summaries(folder)
        self._start_lan_server()
        return True

    def start_listener(self):
        self._save_settings()
        if not self._ensure_worker():
            return
        self.worker.configure(
            summary_detail=self.detail_var.get(), output_dir=self.folder_var.get()
        )
        self.worker.resume()
        self.listen_button.configure(text="Pause listening")
        log.info("listener enabled")
        self._flash("Listening for queued videos.", "good")
        self.refresh_queue()

    def toggle_listener(self):
        if self.worker and self.worker.snapshot().get("listening"):
            self.worker.pause()
            self.listen_button.configure(text="Start listening")
            log.info("listener paused")
            self._flash("Paused. Any video already running will still finish.")
        else:
            self.start_listener()

    def drain_now(self):
        if not self._ensure_worker():
            return
        self.worker.resume()
        self.worker.request_drain()
        self.listen_button.configure(text="Pause listening")
        log.info("manual drain requested")
        self._flash("Working through the queue now…")

    def refresh_everything(self):
        self.refresh_queue()
        self._refresh_summaries(schedule_next=False)
        self._flash("Refreshed.")

    def paste_url(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        url = _clipboard_youtube_url(text)
        if not url:
            self._flash("The clipboard does not hold a YouTube link.", "bad")
            return
        self.url_var.set(url)
        self.clipboard_seen = text.strip()
        self._focus_url()

    def _clipboard_tick(self):
        """Offer a YouTube link sitting on the clipboard, once, while idle."""
        if not self.closing:
            try:
                text = (self.root.clipboard_get() or "").strip()
            except tk.TclError:
                text = ""
            if text and text != self.clipboard_seen and not self.url_var.get().strip():
                self.clipboard_seen = text
                url = _clipboard_youtube_url(text)
                if url:
                    self.url_var.set(url)
                    self._flash("Pulled a YouTube link off your clipboard — press Enter to queue it.")
            self.root.after(2000, self._clipboard_tick)

    def queue_video(self):
        url = self.url_var.get().strip()
        if not url:
            self._flash("Paste a YouTube link first.", "bad")
            self._focus_url()
            return
        try:
            url = pipeline.validate_youtube_url(url)
        except pipeline.PipelineError as exc:
            messagebox.showerror("Not a YouTube URL", str(exc), parent=self.root)
            return
        if not self._ensure_worker():
            return
        prompt = pipeline.normalize_custom_prompt(
            self.prompt_text.get("1.0", "end").strip()
        )
        self._flash("Queueing…")

        def create():
            try:
                issue = self.worker.gh.create_issue(
                    url, body=drain.build_issue_body(prompt)
                )
            except Exception as exc:
                if not self.closing:
                    self.root.after(
                        0,
                        lambda: messagebox.showerror(
                            "Could not queue that video", str(exc), parent=self.root
                        ),
                    )
                log.exception("could not queue %s", url)
                return
            log.info(
                "queued #%s: %s%s",
                issue["number"],
                url,
                " (custom prompt)" if prompt else "",
            )
            self.worker.resume()
            self.worker.request_drain()
            if not self.closing:
                number = issue["number"]
                self.root.after(0, lambda: self._clear_add_form(number))

        threading.Thread(target=create, daemon=True, name="queue-video").start()

    def _clear_add_form(self, issue_number=None):
        self.url_var.set("")
        self.prompt_text.delete("1.0", "end")
        self.listen_button.configure(text="Pause listening")
        if issue_number is not None:
            self._flash(
                f"Queued as #{issue_number}. The summary appears on the right when it is done.",
                "good",
            )
        self.refresh_queue()

    def _start_lan_server(self):
        """Serve the web GUI to other PCs on the network, on our worker."""
        if self.lan_started or not self.worker:
            return
        self.lan_started = True
        dashboard.attach_worker(self.worker, summary_dir=self.folder_var.get())
        try:
            _thread, url = dashboard.serve_in_thread()
        except Exception as exc:
            self.lan_var.set(f"Web GUI unavailable: {exc}")
            log.exception("could not start the LAN web server")
            return
        self.lan_url = url
        self.lan_var.set(f"Web GUI for other PCs: {url}")
        self.lan_open_button.configure(state="normal")
        self.lan_copy_button.configure(state="normal")
        log.info("LAN web GUI serving at %s", url)

    def open_lan_url(self):
        if self.lan_url:
            webbrowser.open(self.lan_url)

    def copy_lan_url(self):
        if not self.lan_url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.lan_url)
        log.info("copied %s to the clipboard", self.lan_url)
        self._flash("Link copied to the clipboard.", "good")

    def choose_folder(self):
        old = Path(self.folder_var.get()).expanduser()
        chosen = filedialog.askdirectory(
            parent=self.root,
            title="Choose where summaries are saved",
            initialdir=str(old if old.exists() else Path.home()),
            mustexist=False,
        )
        if not chosen:
            return
        new = Path(chosen)
        try:
            new.mkdir(parents=True, exist_ok=True)
            self._copy_existing_summaries(old, new)
        except OSError as exc:
            messagebox.showerror("Could not use that folder", str(exc), parent=self.root)
            return
        self.folder_var.set(str(new))
        self._save_settings()
        dashboard.set_summary_dir(new)  # keep the web reader on the same folder
        if self.worker:
            self.worker.configure(output_dir=str(new))
            self._sync_repository_summaries(new)
        self.known_summaries = None  # a new folder is not a batch of new summaries
        self._refresh_summaries(schedule_next=False)
        log.info("summary folder changed to %s", new)
        self._flash(f"Summaries now save to {new}", "good")

    def change_detail(self):
        self._save_settings()
        if self.worker:
            self.worker.configure(summary_detail=self.detail_var.get())
        log.info("summary style set to %s", self.detail_var.get())
        self._flash(f"Summary style set to {self.detail_var.get()}.")

    def change_auto_start(self):
        self._save_settings()

    def change_notify_sound(self):
        self._save_settings()

    def _copy_existing_summaries(self, old: Path, new: Path):
        if not old.is_dir() or old.resolve() == new.resolve():
            return
        for source in old.glob("*.txt"):
            target = new / source.name
            if not target.exists():
                shutil.copy2(source, target)

    def _import_legacy_summaries(self, destination: Path):
        legacy_dir = executable_dir() / drain.SUMMARY_DIR
        if not legacy_dir.is_dir() or legacy_dir.resolve() == destination.resolve():
            return
        for source in legacy_dir.glob("*.md"):
            target = destination / f"{source.stem}.txt"
            if not target.exists():
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _sync_repository_summaries(self, destination: Path):
        if self.summary_syncing or not self.worker:
            return
        self.summary_syncing = True

        def sync():
            try:
                count = drain.import_repository_summaries(
                    self.worker.gh, destination
                )
                log.info("synced %d existing summary file(s) from GitHub", count)
            except Exception:
                log.exception("could not sync existing summaries from GitHub")
            finally:
                self.summary_syncing = False

        threading.Thread(target=sync, daemon=True, name="summary-import").start()

    # ----------------------------------------------------------------- queue

    def refresh_queue(self):
        if self.queue_refreshing or not self.worker:
            return
        self.queue_refreshing = True

        def fetch():
            try:
                issues = self.worker.gh.open_issues()
                error = None
            except Exception as exc:
                issues, error = [], str(exc)
            if not self.closing:
                try:
                    self.root.after(0, lambda: self._show_queue(issues, error))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=fetch, daemon=True, name="queue-refresh").start()

    def _show_queue(self, issues, error):
        self.queue_refreshing = False
        self.queue_tree.delete(*self.queue_tree.get_children())
        self.queue_links.clear()
        if error:
            self.queue_tree.insert(
                "", "end", values=("", "Error", _one_line(error)), tags=("failed",)
            )
            return
        for issue in issues:
            labels = {label["name"] for label in issue.get("labels", [])}
            failed = drain.SKIP_LABEL in labels
            item = self.queue_tree.insert(
                "",
                "end",
                values=(issue["number"], "Failed" if failed else "Waiting", issue["title"]),
                tags=("failed",) if failed else (),
            )
            self.queue_links[item] = issue.get("html_url", "")

    def open_selected_issue(self, _event=None):
        selected = self.queue_tree.selection()
        if selected:
            url = self.queue_links.get(selected[0])
            if url:
                webbrowser.open(url)

    def _queue_refresh_tick(self):
        self.refresh_queue()
        self.root.after(10000, self._queue_refresh_tick)

    # ------------------------------------------------------------- summaries

    def _refresh_summaries(self, schedule_next=True):
        """Poll the summary folder; announce anything the worker just wrote."""
        folder = Path(self.folder_var.get()).expanduser()
        try:
            paths = sorted(
                folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except OSError:
            paths = []
        names = [path.name for path in paths]
        self.all_summary_paths = paths

        if self.known_summaries is None:
            self.known_summaries = set(names)  # first scan is the baseline
        else:
            fresh = _detect_new(self.known_summaries, names)
            if fresh:
                self.known_summaries.update(fresh)
                self._announce_new_summaries(fresh)

        if names != self.last_summary_names:
            self.last_summary_names = names
            self._render_summary_list()
        else:
            self._refresh_ages()

        if schedule_next:
            self.root.after(3000, self._refresh_summaries)

    def _announce_new_summaries(self, fresh: list[str]):
        newest = fresh[0]
        if len(fresh) == 1:
            message = f"✨ Summary ready: {_pretty_title(newest)}"
        else:
            message = (
                f"✨ {len(fresh)} new summaries — newest: {_pretty_title(newest)}"
            )
        log.info("new summary file(s): %s", ", ".join(fresh))
        self._flash(message, "good")
        self._chime()
        self.filter_var.set("")  # never hide the thing we just announced
        self._render_summary_list()
        self._select_summary_by_name(newest, preview=True)

    def _render_summary_list(self):
        query = self.filter_var.get().strip()
        paths = [p for p in self.all_summary_paths if _matches_filter(p.name, query)]
        keep = self._selected_summary_path()
        self.summary_tree.delete(*self.summary_tree.get_children())
        now = time.time()
        for path in paths:
            try:
                age = now - path.stat().st_mtime
            except OSError:
                age = 0
            self.summary_tree.insert(
                "",
                "end",
                values=(_pretty_title(path.name), _human_age(age)),
                tags=("fresh",) if age < FRESH_SECONDS else (),
            )
        self.summary_paths = paths
        total = len(self.all_summary_paths)
        if not total:
            self.summary_count_var.set("No summaries yet")
        elif len(paths) == total:
            self.summary_count_var.set(f"{total} saved")
        else:
            self.summary_count_var.set(f"{len(paths)} of {total}")
        if keep is not None:
            self._select_summary_by_name(keep.name, preview=False)

    def _refresh_ages(self):
        """Keep the "2m ago" column honest without rebuilding the whole list."""
        now = time.time()
        for item, path in zip(self.summary_tree.get_children(), self.summary_paths):
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            self.summary_tree.set(item, "when", _human_age(age))
            if age >= FRESH_SECONDS and "fresh" in self.summary_tree.item(item, "tags"):
                self.summary_tree.item(item, tags=())

    def _selected_summary_path(self) -> Path | None:
        selected = self.summary_tree.selection()
        if not selected:
            return None
        index = self.summary_tree.index(selected[0])
        if 0 <= index < len(self.summary_paths):
            return self.summary_paths[index]
        return None

    def _select_summary_by_name(self, name: str, preview: bool):
        for item, path in zip(self.summary_tree.get_children(), self.summary_paths):
            if path.name == name:
                self.summary_tree.selection_set(item)
                self.summary_tree.see(item)
                if preview:
                    self._show_preview(path)
                return

    def _on_summary_selected(self, _event=None):
        path = self._selected_summary_path()
        if path is not None and path != self.preview_path:
            self._show_preview(path)

    def _show_preview(self, path: Path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._set_preview(f"Could not read {path.name}: {exc}")
            self.preview_path = None
            return
        if len(text) > PREVIEW_CHAR_LIMIT:
            text = text[:PREVIEW_CHAR_LIMIT] + "\n\n… (truncated — open in editor for the rest)"
        self.preview_path = path
        self._set_preview(text)

    def _set_preview(self, text: str):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.see("1.0")
        self.preview_text.configure(state="disabled")

    def copy_preview(self):
        text = self.preview_text.get("1.0", "end-1c")
        if not text.strip() or self.preview_path is None:
            self._flash("Select a summary first.", "bad")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._flash(f"Copied {self.preview_path.name} to the clipboard.", "good")

    def show_newest_summary(self):
        if not self.all_summary_paths:
            self._flash("No summaries yet.", "bad")
            return
        self.filter_var.set("")
        self._render_summary_list()
        self._select_summary_by_name(self.all_summary_paths[0].name, preview=True)

    def open_selected_summary(self, _event=None):
        path = self._selected_summary_path()
        if path is None:
            self._flash("Select a summary first.", "bad")
            return
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("Could not open summary", str(exc), parent=self.root)

    def open_summary_folder(self):
        path = Path(self.folder_var.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    # ---------------------------------------------------------------- status

    def _refresh_status(self):
        if self.worker:
            snapshot = self.worker.snapshot()
            listening = bool(snapshot.get("listening"))
            state = _one_line(snapshot.get("state", ""), 60)
            self.status_var.set(("Listening — " if listening else "Paused — ") + state)
            self.status_dot.configure(fg=DOT_LISTENING if listening else DOT_PAUSED)
            self.counts_var.set(
                f"{snapshot.get('ok_total', 0)} done · {snapshot.get('failed_total', 0)} failed"
            )
            last_poll = snapshot.get("last_poll")
            result = _one_line(snapshot.get("last_result") or "waiting for first poll")
            if last_poll:
                age = max(0, int(time.time() - float(last_poll)))
                self.last_poll_var.set(f"Last poll {age}s ago · {result}")
            else:
                self.last_poll_var.set(result)
            self.listen_button.configure(
                text="Pause listening" if listening else "Start listening"
            )
            self._set_busy(state.startswith("processing") or state == "draining")
        self.root.after(1000, self._refresh_status)

    def _set_busy(self, busy: bool):
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self.progress.grid()
            self.progress.start(15)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _drain_log_messages(self):
        lines = []
        while True:
            try:
                lines.append(self.log_messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            self.log_text.see("end")
            # Keep the GUI responsive after days of logging.
            if int(self.log_text.index("end-1c").split(".")[0]) > 1000:
                self.log_text.delete("1.0", "200.0")
            self.log_text.configure(state="disabled")
        self.root.after(200, self._drain_log_messages)

    def close(self):
        self.closing = True
        if self.worker:
            self.worker.stop()
        self.root.destroy()


def _smoke_test_lan_server():
    """Serve the web UI on an ephemeral port and fetch a page from it."""
    import urllib.request

    class _StubWorker:  # enough surface for the home page to render
        poll_seconds = 0
        gh = None

        def snapshot(self):
            return {
                "state": "smoke test",
                "last_poll": None,
                "last_result": "",
                "ok_total": 0,
                "failed_total": 0,
            }

    dashboard.attach_worker(_StubWorker(), summary_dir=str(data_dir()))
    _thread, url = dashboard.serve_in_thread(host="127.0.0.1", port=0)
    port = url.rsplit(":", 1)[1]
    deadline = time.monotonic() + 20
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                body = resp.read().decode("utf-8", "replace")
            break
        except Exception:
            if time.monotonic() > deadline:
                raise RuntimeError("bundled web server never answered")
            time.sleep(0.25)
    if 'name="prompt"' not in body:
        raise RuntimeError("the web page is missing the AI prompt box")
    if "Auto-refresh:" not in body:
        raise RuntimeError("the web page is missing the auto-refresh control")


def main():
    load_runtime_env()
    if "--smoke-test" in sys.argv:
        version = drain.pipeline._run_yt_dlp(["--version"], timeout=30)
        if not version.stdout.strip():
            raise RuntimeError("bundled yt-dlp did not return a version")
        # uvicorn imports its loop/protocol modules by name at runtime, so a
        # bundling mistake only shows up when the server actually serves.
        _smoke_test_lan_server()
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return
    if not _acquire_single_instance():
        temp = tk.Tk()
        temp.withdraw()
        messagebox.showinfo(
            "ContentSummarizer",
            "ContentSummarizer is already running.",
            parent=temp,
        )
        temp.destroy()
        return
    messages: queue.Queue[str] = queue.Queue()
    _configure_logging(messages)
    root = tk.Tk()
    ContentSummarizerApp(root, messages)
    log.info("desktop app started; settings: %s", data_dir())
    root.mainloop()


if __name__ == "__main__":
    main()

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

import drain
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

        self.folder_var = tk.StringVar(value=str(self.settings["summary_folder"]))
        self.detail_var = tk.StringVar(value=str(self.settings["summary_detail"]))
        self.auto_start_var = tk.BooleanVar(value=bool(self.settings["auto_start"]))
        self.status_var = tk.StringVar(value="Stopped")
        self.last_poll_var = tk.StringVar(value="Not polled yet")

        root.title("ContentSummarizer")
        root.geometry("920x720")
        root.minsize(760, 590)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()

        self.root.after(150, self._drain_log_messages)
        self.root.after(300, self._refresh_status)
        self.root.after(500, self._refresh_summaries)
        self.root.after(800, self._queue_refresh_tick)
        if self.first_run:
            self.root.after(200, self.choose_folder)
        if self.auto_start_var.get():
            self.root.after(600, self.start_listener)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        ttk.Label(
            header,
            text="ContentSummarizer",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var).grid(
            row=0, column=2, sticky="e", padx=(12, 0)
        )
        ttk.Label(header, textvariable=self.last_poll_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(3, 0)
        )

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

        controls = ttk.Frame(outer)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.listen_button = ttk.Button(
            controls, text="Start listening", command=self.toggle_listener
        )
        self.listen_button.pack(side="left")
        ttk.Button(controls, text="Drain now", command=self.drain_now).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(controls, text="Refresh queue", command=self.refresh_queue).pack(
            side="left", padx=(8, 0)
        )
        ttk.Checkbutton(
            controls,
            text="Start listening when the app opens",
            variable=self.auto_start_var,
            command=self.change_auto_start,
        ).pack(side="right")

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.grid(row=3, column=0, sticky="nsew")

        queue_box = ttk.LabelFrame(panes, text="GitHub queue", padding=8)
        summary_box = ttk.LabelFrame(panes, text="Saved summaries", padding=8)
        panes.add(queue_box, weight=1)
        panes.add(summary_box, weight=1)

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
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        queue_scroll = ttk.Scrollbar(
            queue_box, orient="vertical", command=self.queue_tree.yview
        )
        queue_scroll.grid(row=0, column=1, sticky="ns")
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.queue_tree.bind("<Double-1>", self.open_selected_issue)

        summary_box.rowconfigure(0, weight=1)
        summary_box.columnconfigure(0, weight=1)
        self.summary_list = tk.Listbox(
            summary_box,
            activestyle="dotbox",
            borderwidth=0,
            highlightthickness=0,
        )
        self.summary_list.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(
            summary_box, orient="vertical", command=self.summary_list.yview
        )
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.summary_list.configure(yscrollcommand=summary_scroll.set)
        self.summary_list.bind("<Double-1>", self.open_selected_summary)

        log_box = ttk.LabelFrame(outer, text="Activity", padding=8)
        log_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        log_box.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_box,
            height=8,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="ew")

    def _save_settings(self):
        self.settings = {
            "summary_folder": self.folder_var.get(),
            "summary_detail": normalize_detail(self.detail_var.get()),
            "auto_start": self.auto_start_var.get(),
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
        self.refresh_queue()

    def toggle_listener(self):
        if self.worker and self.worker.snapshot().get("listening"):
            self.worker.pause()
            self.listen_button.configure(text="Start listening")
            log.info("listener paused")
        else:
            self.start_listener()

    def drain_now(self):
        if not self._ensure_worker():
            return
        self.worker.resume()
        self.worker.request_drain()
        self.listen_button.configure(text="Pause listening")
        log.info("manual drain requested")

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
        if self.worker:
            self.worker.configure(output_dir=str(new))
            self._sync_repository_summaries(new)
        self._refresh_summaries(schedule_next=False)
        log.info("summary folder changed to %s", new)

    def change_detail(self):
        self._save_settings()
        if self.worker:
            self.worker.configure(summary_detail=self.detail_var.get())
        log.info("summary style set to %s", self.detail_var.get())

    def change_auto_start(self):
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
            self.queue_tree.insert("", "end", values=("", "Error", error))
            return
        for issue in issues:
            labels = {label["name"] for label in issue.get("labels", [])}
            status = "Failed" if drain.SKIP_LABEL in labels else "Waiting"
            item = self.queue_tree.insert(
                "",
                "end",
                values=(issue["number"], status, issue["title"]),
            )
            self.queue_links[item] = issue.get("html_url", "")

    def open_selected_issue(self, _event=None):
        selected = self.queue_tree.selection()
        if selected:
            url = self.queue_links.get(selected[0])
            if url:
                webbrowser.open(url)

    def _refresh_summaries(self, schedule_next=True):
        folder = Path(self.folder_var.get()).expanduser()
        try:
            paths = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            paths = []
        names = [path.name for path in paths]
        current = [self.summary_list.get(i) for i in range(self.summary_list.size())]
        if names != current:
            self.summary_list.delete(0, "end")
            for name in names:
                self.summary_list.insert("end", name)
            self.summary_paths = paths
        if schedule_next:
            self.root.after(3000, self._refresh_summaries)

    def _queue_refresh_tick(self):
        self.refresh_queue()
        self.root.after(10000, self._queue_refresh_tick)

    def open_selected_summary(self, _event=None):
        selected = self.summary_list.curselection()
        if not selected:
            return
        path = self.summary_paths[selected[0]]
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

    def _refresh_status(self):
        if self.worker:
            snapshot = self.worker.snapshot()
            listening = bool(snapshot.get("listening"))
            self.status_var.set(
                ("Listening — " if listening else "Paused — ")
                + str(snapshot.get("state", ""))
            )
            last_poll = snapshot.get("last_poll")
            result = snapshot.get("last_result") or "waiting for first poll"
            if last_poll:
                age = max(0, int(time.time() - float(last_poll)))
                self.last_poll_var.set(f"Last poll {age}s ago · {result}")
            else:
                self.last_poll_var.set(result)
            self.listen_button.configure(
                text="Pause listening" if listening else "Start listening"
            )
        self.root.after(1000, self._refresh_status)

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


def main():
    load_runtime_env()
    if "--smoke-test" in sys.argv:
        version = drain.pipeline._run_yt_dlp(["--version"], timeout=30)
        if not version.stdout.strip():
            raise RuntimeError("bundled yt-dlp did not return a version")
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

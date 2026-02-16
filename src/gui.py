"""Tkinter GUI for the DrChrono Batch Document Uploader."""

import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from src.auth import ensure_auth
from src.config import ensure_credentials, load_config, load_metatags, save_config
from src.parser import DEFAULT_PATTERN, compile_pattern
from src.processor import process_directory
from src.updater import _fetch_latest_release, _parse_version, self_update
from src.version import __version__


class _QueueWriter(io.TextIOBase):
    """A file-like object that writes to a queue instead of a stream."""

    def __init__(self, q: queue.Queue):
        self._queue = q

    def write(self, text: str):
        if text:
            self._queue.put(text)
        return len(text) if text else 0

    def flush(self):
        pass


def _ensure_credentials_gui(config: dict, root: tk.Tk) -> dict:
    """Prompt for DrChrono credentials via GUI dialogs if missing."""
    if config.get("client_id") and config.get("client_secret"):
        return config

    messagebox.showinfo(
        "DrChrono Setup",
        "No credentials found. You'll need your DrChrono API credentials.\n\n"
        "1. Go to https://drchrono.com/api-management/\n"
        "2. Create a new application\n"
        "3. Set Redirect URI to: http://localhost:8585/callback\n"
        "4. Copy the Client ID and Client Secret",
        parent=root,
    )

    client_id = simpledialog.askstring("Credentials", "Client ID:", parent=root)
    if not client_id:
        return config
    client_secret = simpledialog.askstring("Credentials", "Client Secret:", parent=root, show="*")
    if not client_secret:
        return config

    config["client_id"] = client_id.strip()
    config["client_secret"] = client_secret.strip()
    save_config(config)
    return config


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"DrChrono Batch Document Uploader  v{__version__}")
        self.root.geometry("720x540")
        self.root.minsize(600, 400)

        self._build_ui()
        self._output_queue: queue.Queue = queue.Queue()
        self._running = False
        self._latest_tag: str | None = None

        # Check for updates in background on startup
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _build_ui(self):
        # --- Controls frame ---
        controls = ttk.Frame(self.root, padding=10)
        controls.pack(fill=tk.X)

        # Source directory
        ttk.Label(controls, text="Source Directory:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.source_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.source_var, width=50).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(controls, text="Browse", command=self._browse_source).grid(row=0, column=2)

        # Destination directory
        ttk.Label(controls, text="Destination (optional):").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.dest_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.dest_var, width=50).grid(row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Button(controls, text="Browse", command=self._browse_dest).grid(row=1, column=2)

        # Options row
        opts = ttk.Frame(controls)
        opts.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Dry Run", variable=self.dry_run_var).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(opts, text="Workers:").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=1)
        self.workers_spin = ttk.Spinbox(opts, from_=1, to=8, width=4, textvariable=self.workers_var)
        self.workers_spin.pack(side=tk.LEFT, padx=4)
        ttk.Label(opts, text="(recommended: no more than 8)", foreground="gray").pack(side=tk.LEFT)

        # Buttons row
        btn_row = ttk.Frame(controls)
        btn_row.grid(row=3, column=0, columnspan=3, pady=(12, 0))

        self.upload_btn = ttk.Button(btn_row, text="Upload", command=self._start_upload)
        self.upload_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.update_btn = ttk.Button(btn_row, text="Update Available", command=self._start_update, state=tk.DISABLED)
        self.update_btn.pack(side=tk.LEFT)

        controls.columnconfigure(1, weight=1)

        # --- Output log ---
        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(log_frame, text="Output:").pack(anchor=tk.W)
        self.log = scrolledtext.ScrolledText(log_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Courier", 11))
        self.log.pack(fill=tk.BOTH, expand=True)

    def _browse_source(self):
        path = filedialog.askdirectory(title="Select source directory")
        if path:
            self.source_var.set(path)

    def _browse_dest(self):
        path = filedialog.askdirectory(title="Select destination directory")
        if path:
            self.dest_var.set(path)

    def _log_append(self, text: str):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _poll_queue(self):
        while True:
            try:
                text = self._output_queue.get_nowait()
                self._log_append(text)
            except queue.Empty:
                break
        if self._running:
            self.root.after(100, self._poll_queue)

    def _check_for_update(self):
        """Check for updates in background. Enable button if newer version exists."""
        try:
            release = _fetch_latest_release()
            if release:
                tag = release["tag_name"]
                if _parse_version(tag) > _parse_version(__version__):
                    self._latest_tag = tag
                    self.root.after(0, lambda: self.update_btn.configure(
                        text=f"Update to {tag}", state=tk.NORMAL,
                    ))
        except Exception:
            pass

    def _start_update(self):
        """Run self-update in a background thread, then relaunch."""
        self._running = True
        self.upload_btn.configure(state=tk.DISABLED)
        self.update_btn.configure(state=tk.DISABLED)
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        self.root.after(100, self._poll_queue)

        threading.Thread(target=self._run_update, daemon=True).start()

    def _run_update(self):
        old_stdout = sys.stdout
        sys.stdout = _QueueWriter(self._output_queue)
        try:
            self_update(target_version=self._latest_tag)
            print("\nRestarting...")
        except SystemExit:
            pass
        except Exception as exc:
            print(f"\nUpdate failed: {exc}")
            sys.stdout = old_stdout
            self._running = False
            self.root.after(0, lambda: self.upload_btn.configure(state=tk.NORMAL))
            return
        finally:
            sys.stdout = old_stdout
            self._running = False

        # Relaunch the application
        binary = sys.executable if getattr(sys, "frozen", False) else None
        if binary:
            self.root.after(0, lambda: self._relaunch(binary))
        else:
            self.root.after(0, lambda: self.upload_btn.configure(state=tk.NORMAL))

    def _relaunch(self, binary: str):
        """Close the GUI and relaunch the binary."""
        self.root.destroy()
        subprocess.Popen([binary, "gui"])
        sys.exit(0)

    def _start_upload(self):
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("Missing Directory", "Please select a source directory.", parent=self.root)
            return

        self._running = True
        self.upload_btn.configure(state=tk.DISABLED)
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

        dest = self.dest_var.get().strip() or None
        dry_run = self.dry_run_var.get()
        num_workers = self.workers_var.get()

        self.root.after(100, self._poll_queue)

        thread = threading.Thread(
            target=self._run_upload,
            args=(source, dest, dry_run, num_workers),
            daemon=True,
        )
        thread.start()

    def _run_upload(self, source: str, dest: str | None, dry_run: bool, num_workers: int):
        old_stdout = sys.stdout
        sys.stdout = _QueueWriter(self._output_queue)
        try:
            config = load_config()

            # Check credentials — use GUI dialogs if missing
            if not config.get("client_id") or not config.get("client_secret"):
                # Schedule dialog on main thread and wait
                result: dict = {}
                event = threading.Event()

                def _ask():
                    updated = _ensure_credentials_gui(config, self.root)
                    result.update(updated)
                    event.set()

                self.root.after(0, _ask)
                event.wait()
                config = result
                if not config.get("client_id") or not config.get("client_secret"):
                    print("Setup cancelled — credentials are required.")
                    return

            config = ensure_auth(config)
            metatags = load_metatags()
            pattern_re = compile_pattern(DEFAULT_PATTERN, metatags)

            print(f"Using filename pattern: {DEFAULT_PATTERN}\n")
            process_directory(
                config, source, metatags, pattern_re,
                dry_run=dry_run, dest_dir=dest, num_workers=num_workers,
            )
        except Exception as exc:
            print(f"\nError: {exc}")
        finally:
            sys.stdout = old_stdout
            self._running = False
            self.root.after(0, lambda: self.upload_btn.configure(state=tk.NORMAL))


def launch():
    """Launch the GUI application."""
    root = tk.Tk()
    App(root)
    root.mainloop()

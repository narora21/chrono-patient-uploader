"""Tkinter GUI for the DrChrono Batch Document Uploader."""

import io
import os
import platform
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from src.auth import ensure_auth
from src.config import (
    ensure_credentials, load_config, load_metatags, load_settings,
    save_config, save_metatags, save_settings,
)
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

        # Restore saved directory paths
        settings = load_settings()
        if settings.get("source_directory"):
            self.source_var.set(settings["source_directory"])
        if settings.get("dest_directory"):
            self.dest_var.set(settings["dest_directory"])

        # Check for updates in background on startup
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _build_ui(self):
        # --- Notebook with tabs ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_upload_tab()
        self._build_metatags_tab()

    def _build_upload_tab(self):
        upload_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(upload_tab, text="Upload")

        # --- Controls frame ---
        controls = ttk.Frame(upload_tab)
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

        self.update_btn = ttk.Button(btn_row, text="Update", command=self._start_update)
        # Hidden until an update is detected
        self._btn_row = btn_row

        controls.columnconfigure(1, weight=1)

        # --- Output log ---
        log_frame = ttk.Frame(upload_tab)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        ttk.Label(log_frame, text="Output:").pack(anchor=tk.W)
        self.log = scrolledtext.ScrolledText(log_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Courier", 11))
        self.log.pack(fill=tk.BOTH, expand=True)

    def _build_metatags_tab(self):
        meta_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(meta_tab, text="Metatags")

        ttk.Label(meta_tab, text="Configure tag code to category mappings. These are saved to metatag.json.").pack(anchor=tk.W, pady=(0, 8))

        # Treeview for tag list
        tree_frame = ttk.Frame(meta_tab)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("code", "category")
        self.meta_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.meta_tree.heading("code", text="Tag Code")
        self.meta_tree.heading("category", text="Category")
        self.meta_tree.column("code", width=100, anchor=tk.W)
        self.meta_tree.column("category", width=300, anchor=tk.W)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.meta_tree.yview)
        self.meta_tree.configure(yscrollcommand=scrollbar.set)
        self.meta_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Buttons
        btn_frame = ttk.Frame(meta_tab)
        btn_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(btn_frame, text="Add", command=self._meta_add).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Edit", command=self._meta_edit).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Delete", command=self._meta_delete).pack(side=tk.LEFT)

        self._meta_load()

    def _meta_load(self):
        """Load metatags from file into the treeview."""
        self.meta_tree.delete(*self.meta_tree.get_children())
        try:
            metatags = load_metatags()
        except SystemExit:
            metatags = {}
        for code, category in sorted(metatags.items()):
            self.meta_tree.insert("", tk.END, values=(code, category))

    def _meta_save(self):
        """Save current treeview contents to metatag.json."""
        metatags = {}
        for item in self.meta_tree.get_children():
            code, category = self.meta_tree.item(item, "values")
            metatags[code] = category
        save_metatags(metatags)

    def _meta_add(self):
        code = simpledialog.askstring("Add Metatag", "Tag code (e.g. L, HP, CO):", parent=self.root)
        if not code:
            return
        code = code.strip().upper()
        # Check for duplicate
        for item in self.meta_tree.get_children():
            if self.meta_tree.item(item, "values")[0] == code:
                messagebox.showwarning("Duplicate", f"Tag code '{code}' already exists.", parent=self.root)
                return
        category = simpledialog.askstring("Add Metatag", f"Category for '{code}' (e.g. laboratory, radiology):", parent=self.root)
        if not category:
            return
        self.meta_tree.insert("", tk.END, values=(code, category.strip()))
        self._meta_save()

    def _meta_edit(self):
        selected = self.meta_tree.selection()
        if not selected:
            messagebox.showinfo("No Selection", "Select a metatag to edit.", parent=self.root)
            return
        item = selected[0]
        old_code, old_category = self.meta_tree.item(item, "values")

        code = simpledialog.askstring("Edit Metatag", "Tag code:", parent=self.root, initialvalue=old_code)
        if not code:
            return
        code = code.strip().upper()
        # Check for duplicate if code changed
        if code != old_code:
            for other in self.meta_tree.get_children():
                if other != item and self.meta_tree.item(other, "values")[0] == code:
                    messagebox.showwarning("Duplicate", f"Tag code '{code}' already exists.", parent=self.root)
                    return

        category = simpledialog.askstring("Edit Metatag", f"Category for '{code}':", parent=self.root, initialvalue=old_category)
        if not category:
            return
        self.meta_tree.item(item, values=(code, category.strip()))
        self._meta_save()

    def _meta_delete(self):
        selected = self.meta_tree.selection()
        if not selected:
            messagebox.showinfo("No Selection", "Select a metatag to delete.", parent=self.root)
            return
        item = selected[0]
        code = self.meta_tree.item(item, "values")[0]
        if messagebox.askyesno("Confirm Delete", f"Delete tag '{code}'?", parent=self.root):
            self.meta_tree.delete(item)
            self._meta_save()

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
                    self.root.after(0, lambda: (
                        self.update_btn.configure(text=f"Update to {tag}"),
                        self.update_btn.pack(side=tk.LEFT),
                    ))
        except Exception:
            pass

    def _start_update(self):
        """Run self-update in a background thread, then relaunch."""
        self._running = True
        self.upload_btn.configure(state=tk.DISABLED)
        self.update_btn.pack_forget()
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        self.root.after(100, self._poll_queue)

        threading.Thread(target=self._run_update, daemon=True).start()

    def _run_update(self):
        old_stdout = sys.stdout
        sys.stdout = _QueueWriter(self._output_queue)
        success = False
        try:
            self_update(target_version=self._latest_tag)
            success = True
        except SystemExit:
            success = True
        except Exception as exc:
            print(f"\nUpdate failed: {exc}")
        finally:
            sys.stdout = old_stdout
            self._running = False

        if success:
            self.root.after(0, self._show_update_complete)
        else:
            self.root.after(0, lambda: self.upload_btn.configure(state=tk.NORMAL))

    def _show_update_complete(self):
        messagebox.showinfo(
            "Update Complete",
            "Update installed successfully. Please relaunch the application to use the new version.",
            parent=self.root,
        )
        self.root.destroy()

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

        # Save directory paths for next session
        save_settings({
            "source_directory": source,
            "dest_directory": dest or "",
        })

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


def install_shortcut():
    """Create a desktop shortcut to launch the GUI."""
    if not getattr(sys, "frozen", False):
        print("Error: install-shortcut only works with the standalone executable.")
        print("You're running from source — launch with: python -m src.main gui")
        sys.exit(1)

    binary = sys.executable
    system = platform.system()
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")

    if system == "Darwin":
        app_path = os.path.join(desktop, "DrChrono Uploader.app")
        if os.path.exists(app_path):
            print(f"Shortcut already exists: {app_path}")
            return
        macos_dir = os.path.join(app_path, "Contents", "MacOS")
        os.makedirs(macos_dir, exist_ok=True)

        script_path = os.path.join(macos_dir, "launcher")
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f'exec "{binary}" gui\n')
        os.chmod(script_path, 0o755)

        plist_path = os.path.join(app_path, "Contents", "Info.plist")
        with open(plist_path, "w") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n')
            f.write('<plist version="1.0">\n<dict>\n')
            f.write('  <key>CFBundleName</key>\n  <string>DrChrono Uploader</string>\n')
            f.write('  <key>CFBundleExecutable</key>\n  <string>launcher</string>\n')
            f.write('  <key>CFBundleIdentifier</key>\n  <string>com.chrono-uploader.gui</string>\n')
            f.write('  <key>CFBundlePackageType</key>\n  <string>APPL</string>\n')
            f.write('  <key>LSUIElement</key>\n  <false/>\n')
            f.write('</dict>\n</plist>\n')
        print(f"Desktop shortcut created: {app_path}")

    elif system == "Windows":
        lnk_path = os.path.join(desktop, "DrChrono Uploader.lnk")
        if os.path.exists(lnk_path):
            print(f"Shortcut already exists: {lnk_path}")
            return
        ps_cmd = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$s = $ws.CreateShortcut("{lnk_path}"); '
            f'$s.TargetPath = "{binary}"; '
            f'$s.Arguments = "gui"; '
            f'$s.WorkingDirectory = "{os.path.dirname(binary)}"; '
            f'$s.Description = "DrChrono Batch Document Uploader"; '
            f'$s.Save()'
        )
        import subprocess
        subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
        print(f"Desktop shortcut created: {lnk_path}")

    elif system == "Linux":
        desktop_file = os.path.join(desktop, "chrono-uploader.desktop")
        if os.path.exists(desktop_file):
            print(f"Shortcut already exists: {desktop_file}")
            return
        with open(desktop_file, "w") as f:
            f.write("[Desktop Entry]\n")
            f.write("Type=Application\n")
            f.write("Name=DrChrono Uploader\n")
            f.write(f"Exec={binary} gui\n")
            f.write("Terminal=false\n")
            f.write("Comment=DrChrono Batch Document Uploader\n")
        os.chmod(desktop_file, 0o755)
        print(f"Desktop shortcut created: {desktop_file}")

    else:
        print(f"Error: Unsupported platform '{system}'.")
        sys.exit(1)


def launch():
    """Launch the GUI application."""
    root = tk.Tk()
    App(root)
    root.mainloop()

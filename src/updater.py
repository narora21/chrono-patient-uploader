"""Self-update: download and replace the running binary with the latest release."""

import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

import requests

from src.version import __version__

REPO = "narora21/chrono-patient-uploader"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

# ANSI escape codes
_BOLD = "\033[1m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse a version tag like 'v1.2.3' into (1, 2, 3)."""
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def _get_platform_archive() -> str:
    """Return the archive filename for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return "chrono-uploader-mac.tar.gz"
    elif system == "Linux":
        return "chrono-uploader-linux.tar.gz"
    elif system == "Windows":
        return "chrono-uploader-win.zip"
    else:
        print(f"Error: Unsupported platform '{system}'.")
        sys.exit(1)


def _get_binary_path() -> str:
    """Get the path of the currently running executable."""
    # PyInstaller sets sys._MEIPASS; frozen binary path is sys.executable
    if getattr(sys, "frozen", False):
        return sys.executable
    # Running from source — not a real update target
    print("Error: Self-update only works with the standalone executable.")
    print("You're running from source. Use 'git pull' to update instead.")
    sys.exit(1)


def check_for_update():
    """Print a notice if a newer version is available. Non-blocking — silently does nothing on error."""
    try:
        resp = requests.get(LATEST_URL, timeout=5)
        resp.raise_for_status()
        latest_tag = resp.json()["tag_name"]
        if _parse_version(latest_tag) > _parse_version(__version__):
            print(f"{_BOLD}{_YELLOW}A new version is available: {latest_tag} (current: {__version__}){_RESET}")
            print(f"{_BOLD}{_YELLOW}Run: chrono-uploader update{_RESET}\n")
    except Exception:
        pass


def self_update():
    """Check for a new release and replace the current binary if available."""
    print(f"Current version: {__version__}")
    print("Checking for updates...")

    try:
        resp = requests.get(LATEST_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Error: Could not check for updates: {exc}")
        sys.exit(1)

    release = resp.json()
    latest_tag = release["tag_name"]
    latest_version = _parse_version(latest_tag)
    current_version = _parse_version(__version__)

    if latest_version <= current_version:
        print(f"Already up to date (latest: {latest_tag}).")
        return

    print(f"New version available: {latest_tag}")

    archive_name = _get_platform_archive()
    download_url = None
    for asset in release.get("assets", []):
        if asset["name"] == archive_name:
            download_url = asset["browser_download_url"]
            break

    if not download_url:
        print(f"Error: No release asset found for '{archive_name}'.")
        sys.exit(1)

    binary_path = _get_binary_path()
    install_dir = os.path.dirname(binary_path)

    print(f"Downloading {archive_name}...")
    tmpdir = tempfile.mkdtemp()
    try:
        archive_path = os.path.join(tmpdir, archive_name)
        with requests.get(download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(archive_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir)

        if archive_name.endswith(".tar.gz"):
            import tarfile
            with tarfile.open(archive_path) as tar:
                tar.extractall(extract_dir)
        elif archive_name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)

        # Find the new binary inside the extracted folder
        system = platform.system()
        exe_name = "chrono-uploader.exe" if system == "Windows" else "chrono-uploader"

        new_binary = None
        for root, dirs, files in os.walk(extract_dir):
            if exe_name in files:
                new_binary = os.path.join(root, exe_name)
                break

        if not new_binary:
            print("Error: Could not find executable in downloaded archive.")
            sys.exit(1)

        # Replace the current binary
        print(f"Updating {binary_path}...")
        if system == "Windows":
            # Windows can't overwrite a running exe; rename old first
            old_path = binary_path + ".old"
            os.replace(binary_path, old_path)
            shutil.copy2(new_binary, binary_path)
            os.remove(old_path)
        else:
            shutil.copy2(new_binary, binary_path)
            os.chmod(binary_path, os.stat(binary_path).st_mode | stat.S_IEXEC)

        # Also update bundled files (metatag.json, README.md)
        for fname in ("metatag.json", "README.md"):
            for root, dirs, files in os.walk(extract_dir):
                if fname in files:
                    shutil.copy2(os.path.join(root, fname), os.path.join(install_dir, fname))
                    break

        # Verify
        print("Verifying update...")
        result = subprocess.run([binary_path, "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Updated successfully to {result.stdout.strip()}.")
        else:
            print("Warning: Update installed but verification failed.")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

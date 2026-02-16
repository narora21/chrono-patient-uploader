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
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases"

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


def uninstall():
    """Remove chrono-uploader by deleting its install directory."""
    if not getattr(sys, "frozen", False):
        print("Error: Uninstall only works with the standalone executable.")
        print("You're running from source — just delete the project directory.")
        sys.exit(1)

    install_dir = os.path.dirname(sys.executable)

    confirm = input(f"This will delete {install_dir} and all its contents. Continue? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    system = platform.system()
    if system == "Windows":
        # Windows can't delete a running exe — use a cmd script to clean up after exit
        bat_path = os.path.join(tempfile.gettempdir(), "_chrono_uninstall.cmd")
        with open(bat_path, "w") as f:
            f.write("@echo off\r\n")
            f.write(":wait\r\n")
            f.write("timeout /t 1 /nobreak >nul\r\n")
            f.write(f'rmdir /s /q "{install_dir}" >nul 2>&1\r\n')
            f.write(f'if exist "{install_dir}" goto wait\r\n')
            f.write("echo chrono-uploader has been uninstalled.\r\n")
            f.write(f'del "%~f0"\r\n')
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        print("Uninstalling... the folder will be removed momentarily.")
        sys.exit(0)
    else:
        shutil.rmtree(install_dir)
        print("chrono-uploader has been uninstalled.")


def cleanup_old_binary():
    """Delete leftover .old binary from a previous update. Called on startup."""
    if not getattr(sys, "frozen", False):
        return
    old_path = sys.executable + ".old"
    try:
        if os.path.exists(old_path):
            os.remove(old_path)
    except OSError:
        pass


def _fetch_latest_release():
    """Fetch all releases and return the one with the highest semver tag."""
    resp = requests.get(RELEASES_URL, timeout=10)
    resp.raise_for_status()
    releases = resp.json()

    best = None
    best_version = (-1,)
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        try:
            ver = _parse_version(tag)
        except (ValueError, IndexError):
            continue
        if ver > best_version:
            best_version = ver
            best = rel

    return best


def check_for_update():
    """Print a notice if a newer version is available. Non-blocking — silently does nothing on error."""
    try:
        release = _fetch_latest_release()
        if release:
            latest_tag = release["tag_name"]
            if _parse_version(latest_tag) > _parse_version(__version__):
                print(f"{_BOLD}{_YELLOW}A new version is available: {latest_tag} (current: {__version__}){_RESET}")
                print(f"{_BOLD}{_YELLOW}Run: chrono-uploader update{_RESET}\n")
    except Exception:
        pass


def self_update(target_version=None):
    """Check for a new release and replace the current binary if available."""
    print(f"Current version: {__version__}")

    if target_version:
        # Fetch a specific version
        tag = target_version if target_version.startswith("v") else f"v{target_version}"
        release_url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
        print(f"Fetching version {tag}...")
        try:
            resp = requests.get(release_url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"Error: Could not fetch release: {exc}")
            sys.exit(1)
        release = resp.json()
    else:
        print("Checking for updates...")
        try:
            release = _fetch_latest_release()
        except requests.RequestException as exc:
            print(f"Error: Could not fetch releases: {exc}")
            sys.exit(1)
        if not release:
            print("No releases found.")
            return

    release_tag = release["tag_name"]

    if not target_version:
        release_version = _parse_version(release_tag)
        current_version = _parse_version(__version__)
        if release_version <= current_version:
            print(f"Already up to date (latest: {release_tag}).")
            return

    print(f"Installing version: {release_tag}")

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

        # Copy bundled files (these aren't locked, safe to overwrite)
        for fname in ("metatag.json", "README.md"):
            for root, dirs, files in os.walk(extract_dir):
                if fname in files:
                    shutil.copy2(os.path.join(root, fname), os.path.join(install_dir, fname))
                    break

        # Replace the current binary
        print(f"Updating {binary_path}...")
        if system == "Windows":
            # Windows locks the running exe — rename it out of the way, copy new one in
            old_path = binary_path + ".old"
            os.rename(binary_path, old_path)
            shutil.copy2(new_binary, binary_path)
            print(f"Updated successfully! The old version will be cleaned up on next run.")
        else:
            shutil.copy2(new_binary, binary_path)
            os.chmod(binary_path, os.stat(binary_path).st_mode | stat.S_IEXEC)
            # Re-sign to clear macOS code signing cache (prevents "killed" on first run)
            if system == "Darwin":
                subprocess.run(["codesign", "--force", "--sign", "-", binary_path],
                               capture_output=True)
            print("Verifying update...")
            result = subprocess.run([binary_path, "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"Updated successfully to {result.stdout.strip()}.")
            else:
                print("Warning: Update installed but verification failed.")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
